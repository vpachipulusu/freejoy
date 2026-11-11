import { NextResponse, type NextRequest } from 'next/server';
import { recordOutboundClick, type OutboundClickResult } from '@/lib/api/outbound';
import { logger } from '@/lib/logger';
import { disclosureBouncePath, isExactMarker, UNDISCLOSED_PARAM } from './disclosure';
import {
  classifyUserAgent,
  isOfferId,
  isOfferSlug,
  parseHttpsDestination,
  referrerHost,
} from './validation';

/**
 * The decision behind `GET /go/[offerId]`, kept out of the route file so it is unit tested and counted
 * in coverage.
 *
 * Rules this module enforces:
 * - Only the path id and one query parameter are read: exactly `nd=1` ("not disclosed": the link was rendered
 *   on a page that showed no "Ad" label). It is forwarded to the backend as `commission_disclosed: false` and
 *   can only make the answer stricter: if the offer earns commission the visitor is sent back to the offer
 *   page instead of to the brand. Any other value, a repeated `nd` and every other parameter are ignored. No
 *   query parameter, body or custom header can influence the destination.
 * - The Location is built from a `URL` object that passed `parseHttpsDestination` (https only, no user
 *   info), never by string concatenation.
 * - Every response is `Cache-Control: no-store` and sets no cookie.
 * - A successful redirect is a 307 with `Referrer-Policy: strict-origin-when-cross-origin`.
 */

export const REFERRER_POLICY = 'strict-origin-when-cross-origin';
const NO_STORE = 'no-store';
const UNAVAILABLE_RETRY_SECONDS = '30';
const ENDED_REDIRECT_STATUS = 302;
const DISCLOSURE_REDIRECT_STATUS = 303;
const REDIRECT_STATUS = 307;

function textResponse(status: number, body: string | null, headers: Record<string, string> = {}) {
  return new NextResponse(body, {
    status,
    headers: { 'Cache-Control': NO_STORE, 'Content-Type': 'text/plain; charset=utf-8', ...headers },
  });
}

function unavailable() {
  return textResponse(503, 'This link is not available right now. Please try again later.', {
    'Retry-After': UNAVAILABLE_RETRY_SECONDS,
  });
}

function notFound() {
  return textResponse(404, 'This offer is not available.');
}

function isPrefetch(request: NextRequest): boolean {
  const purpose = `${request.headers.get('sec-purpose') ?? ''} ${request.headers.get('purpose') ?? ''}`;
  return /prefetch|prerender/i.test(purpose);
}

/**
 * Fetch Metadata: only a top-level navigation is a click. An image, iframe, script, fetch() or other
 * subresource request carries `Sec-Fetch-Mode` / `Sec-Fetch-Dest` values other than `navigate` /
 * `document`, so a hostile page cannot inflate click counts with them. A client that sends neither
 * header (an old browser, curl) is allowed.
 */
function isNavigation(request: NextRequest): boolean {
  const mode = request.headers.get('sec-fetch-mode');
  const dest = request.headers.get('sec-fetch-dest');
  return (mode === null || mode === 'navigate') && (dest === null || dest === 'document');
}

/**
 * A RELATIVE Location (`/freebies/<slug>?ended=1`): the browser resolves it against the host it asked,
 * so neither `Host` nor `X-Forwarded-Host` can put another origin into the redirect. The slug is checked
 * against the slug pattern first, so nothing else can enter the path.
 */
function endedRedirect(slug: string): NextResponse {
  if (!isOfferSlug(slug)) return notFound();
  return new NextResponse(null, {
    status: ENDED_REDIRECT_STATUS,
    headers: { Location: `/freebies/${slug}?ended=1`, 'Cache-Control': NO_STORE },
  });
}

/** True only for exactly one `nd` parameter with the exact value `1`. */
function pageShowedNoDisclosure(request: NextRequest): boolean {
  return isExactMarker(request.nextUrl.searchParams.getAll(UNDISCLOSED_PARAM));
}

/**
 * The page the visitor came from showed no commission disclosure but this offer earns one (its cached copy was
 * stale). No click was recorded and no destination reached this server. The visitor is sent back to the offer page
 * with a RELATIVE Location built from a checked slug and `?ad=1`, which makes that page show the disclosure and a
 * plain `/go/<id>` link whatever its cached flag says (see `./disclosure`), so this cannot loop. This route never
 * touches the catalogue cache: an anonymous request must not be able to expire it. The stale entry ages out on its
 * own within the revalidate window (`fetchOffer`).
 */
function disclosureRedirect(offerId: string, slug: string): NextResponse {
  if (!isOfferSlug(slug)) return notFound();
  logger.info('Outbound click sent back for a commission disclosure', { offerId });
  return new NextResponse(null, {
    status: DISCLOSURE_REDIRECT_STATUS,
    headers: { Location: disclosureBouncePath(slug), 'Cache-Control': NO_STORE },
  });
}

function destinationRedirect(offerId: string, destination: string): NextResponse {
  const target = parseHttpsDestination(destination);
  if (target === null) {
    // Never log the destination: it can carry a query string.
    logger.error('Outbound destination refused by the route', { offerId });
    return unavailable();
  }
  return NextResponse.redirect(target, {
    status: REDIRECT_STATUS,
    headers: { 'Cache-Control': NO_STORE, 'Referrer-Policy': REFERRER_POLICY },
  });
}

function respondTo(
  request: NextRequest,
  offerId: string,
  result: OutboundClickResult
): NextResponse {
  switch (result.kind) {
    case 'redirect':
      return destinationRedirect(offerId, result.destination);
    case 'ended':
      return endedRedirect(result.slug);
    case 'disclosure_required':
      return disclosureRedirect(offerId, result.slug);
    case 'not_found':
      return notFound();
    case 'rate_limited':
      return textResponse(429, 'Too many requests. Please try again shortly.', {
        'Retry-After': String(result.retryAfterSeconds),
      });
    default:
      return unavailable();
  }
}

/** Handles one outbound request. `offerId` is the raw path segment; nothing else is trusted. */
export async function handleOutboundRequest(
  request: NextRequest,
  offerId: string
): Promise<NextResponse> {
  if (!isOfferId(offerId)) return notFound();
  // Neither a subresource request nor a speculative load of the link is a click.
  if (!isNavigation(request) || isPrefetch(request)) return textResponse(204, null);
  const result = await recordOutboundClick(offerId, {
    referrerHost: referrerHost(request.headers.get('referer')),
    userAgentClass: classifyUserAgent(request.headers.get('user-agent')),
    ...(pageShowedNoDisclosure(request) ? { commissionDisclosed: false as const } : {}),
  });
  return respondTo(request, offerId, result);
}
