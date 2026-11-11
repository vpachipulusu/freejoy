import 'server-only';

import { logger } from '@/lib/logger';
import type { UserAgentClass } from '@/lib/outbound/validation';
import { getBackendClient } from './server';
import { recordClick } from './generated';

/**
 * Server-only outbound click (`POST /v1/catalogue/offers/{id}/click`, internal key, no session).
 *
 * Used only by the `/go/[offerId]` route handler. The backend records one click event and answers with
 * the stored affiliate destination. This wrapper folds every outcome into a small union so the route
 * never sees backend text, and it never logs the destination.
 *
 * It uses the client directly instead of `callBackend` because the 410 body carries the offer slug
 * (needed to send the visitor to the "ended" page), which `callBackend` drops from errors.
 */

export const CLICK_REQUEST_TIMEOUT_MS = 5_000;
export const DEFAULT_RETRY_AFTER_SECONDS = 60;
const MAX_RETRY_AFTER_SECONDS = 3_600;

export type OutboundClickResult =
  | { kind: 'redirect'; destination: string }
  | { kind: 'ended'; slug: string }
  | { kind: 'disclosure_required'; slug: string }
  | { kind: 'not_found' }
  | { kind: 'rate_limited'; retryAfterSeconds: number }
  | { kind: 'unavailable' };

export interface ClickDetails {
  referrerHost: string | null;
  userAgentClass: UserAgentClass;
  /**
   * Set to false only when the visitor clicked a link whose page showed no commission disclosure. Left out
   * otherwise, so the backend default (disclosed) applies. It can only make the backend stricter.
   */
  commissionDisclosed?: false;
}

const UNAVAILABLE: OutboundClickResult = { kind: 'unavailable' };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function destinationOf(body: unknown): OutboundClickResult {
  const data = isRecord(body) ? body.data : undefined;
  const destination = isRecord(data) ? data.destination_url : undefined;
  return typeof destination === 'string' && destination !== ''
    ? { kind: 'redirect', destination }
    : UNAVAILABLE;
}

function slugOf(body: unknown): OutboundClickResult {
  const data = isRecord(body) && body.expired === true ? body.data : undefined;
  const slug = isRecord(data) ? data.slug : undefined;
  // An ended offer whose slug cannot be read is treated as gone, not as an error.
  return typeof slug === 'string' ? { kind: 'ended', slug } : { kind: 'not_found' };
}

/** The 409 `disclosure_required` body: the offer earns commission the page did not disclose. */
function disclosureOf(body: unknown): OutboundClickResult {
  if (!isRecord(body) || body.code !== 'disclosure_required') return UNAVAILABLE;
  const slug = isRecord(body.data) ? body.data.slug : undefined;
  return typeof slug === 'string' ? { kind: 'disclosure_required', slug } : UNAVAILABLE;
}

/** The backend's Retry-After in whole seconds, clamped; anything unusable becomes the default. */
function retryAfterOf(header: string | null): number {
  if (header === null || !/^\d+$/.test(header.trim())) return DEFAULT_RETRY_AFTER_SECONDS;
  const seconds = Number.parseInt(header.trim(), 10);
  if (seconds < 1) return DEFAULT_RETRY_AFTER_SECONDS;
  return Math.min(seconds, MAX_RETRY_AFTER_SECONDS);
}

function outcomeOf(response: Response, body: unknown): OutboundClickResult {
  const status = response.status;
  if (status === 200) return destinationOf(body);
  if (status === 404) return { kind: 'not_found' };
  if (status === 410) return slugOf(body);
  if (status === 409) return disclosureOf(body);
  if (status === 429) {
    return {
      kind: 'rate_limited',
      retryAfterSeconds: retryAfterOf(response.headers.get('Retry-After')),
    };
  }
  return UNAVAILABLE;
}

export async function recordOutboundClick(
  offerId: string,
  details: ClickDetails
): Promise<OutboundClickResult> {
  try {
    const client = await getBackendClient({ withSession: false, requireInternalKey: true });
    const result = await recordClick({
      client,
      path: { offer_id: offerId },
      body: {
        referrer_host: details.referrerHost,
        user_agent_class: details.userAgentClass,
        ...(details.commissionDisclosed === false ? { commission_disclosed: false } : {}),
      },
      signal: AbortSignal.timeout(CLICK_REQUEST_TIMEOUT_MS),
    });
    if (!result.response) return UNAVAILABLE;
    const body: unknown = result.response.ok ? result.data : result.error;
    return outcomeOf(result.response, body);
  } catch (error) {
    logger.warn('Outbound click request failed', {
      reason: error instanceof Error ? error.name : 'unknown',
    });
    return UNAVAILABLE;
  }
}
