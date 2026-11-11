import 'server-only';

import { AsyncLocalStorage } from 'node:async_hooks';
import { unstable_cache } from 'next/cache';
import { CatalogueRateLimitedError, CatalogueUnavailableError } from '@/lib/catalogue/errors';
import { isValidSlug } from '@/lib/catalogue/slug';
import { logger } from '@/lib/logger';
import { callBackend, readTrustedClientIp, type ApiResult } from './server';
import {
  listCategories,
  listOffers,
  readOffer,
  type CategoryData,
  type ExpiredOfferData,
  type ListOffersData,
  type OfferData,
  type OfferDetailData,
  type OfferListData,
} from './generated';

/**
 * Server-only reads of the public catalogue (`GET /v1/catalogue/*`, internal key, no session).
 *
 * Every read goes through `callBackend(..., { withSession: false, shared: true })` inside
 * `unstable_cache` (tag `catalogue`, 60 s), so a popular page costs the backend one request a minute
 * however many visitors arrive. Failures THROW `CatalogueUnavailableError` instead of returning an
 * empty result: `unstable_cache` never stores a thrown error, so an outage is not remembered and the
 * next request tries again. Backend text never leaves this module; it is logged and replaced by the
 * curated message in `@/lib/catalogue/errors`.
 *
 * Searches with text (`q`) skip the cache so a visitor's words are never a cache key; they are still
 * rate limited per visitor through `X-Client-IP`.
 */

export type {
  CategoryData as ApiCategory,
  ExpiredOfferData as ApiEndedOffer,
  OfferData as ApiOffer,
  OfferDetailData as ApiOfferDetail,
};

export const CATALOGUE_CACHE_TAG = 'catalogue';
export const CATALOGUE_REVALIDATE_SECONDS = 60;
export const CATALOGUE_REQUEST_TIMEOUT_MS = 3_000;

type ListQuery = NonNullable<ListOffersData['query']>;

/** The filters a catalogue read can carry. Every field optional; omitted means "no filter". */
export interface OfferListParams {
  q?: string;
  category?: string;
  types?: readonly NonNullable<ListQuery['type']>[number][];
  postage?: 'free';
  featured?: boolean;
  sort?: 'newest' | 'ending_soon' | 'a_z';
  limit?: number;
  offset?: number;
}

export type OfferReadResult =
  | { kind: 'found'; offer: OfferDetailData }
  | { kind: 'gone'; ended: ExpiredOfferData | null }
  | { kind: 'not_found' };

/** The visitor address for the read in flight. Held outside the cache key on purpose. */
const visitorIp = new AsyncLocalStorage<string | undefined>();

async function withVisitorIp<T>(run: () => Promise<T>): Promise<T> {
  let ip: string | undefined;
  try {
    ip = await readTrustedClientIp();
  } catch {
    ip = undefined; // no request scope (build, tests): nothing to forward
  }
  return visitorIp.run(ip, run);
}

function backendOptions() {
  return { withSession: false, shared: true, clientIp: visitorIp.getStore() } as const;
}

function timeoutSignal(): AbortSignal {
  return AbortSignal.timeout(CATALOGUE_REQUEST_TIMEOUT_MS);
}

/** Logs the failure (never the search text) and throws the typed error callers turn into an error state. */
function fail(operation: string, result: Extract<ApiResult<unknown>, { success: false }>): never {
  logger.warn('Catalogue read failed', { operation, status: result.status, error: result.error });
  if (result.status === 429) throw new CatalogueRateLimitedError();
  throw new CatalogueUnavailableError();
}

/** Stable key order and no undefined values, so equal filters share one cache entry. */
function toQuery(params: OfferListParams): ListQuery {
  const query: ListQuery = {};
  if (params.q) query.q = params.q;
  if (params.category && isValidSlug(params.category)) query.category = params.category;
  if (params.types && params.types.length > 0) query.type = [...params.types];
  if (params.postage) query.postage = params.postage;
  if (params.featured !== undefined) query.featured = params.featured;
  if (params.sort) query.sort = params.sort;
  if (params.limit !== undefined) query.limit = params.limit;
  if (params.offset !== undefined) query.offset = params.offset;
  return query;
}

async function loadOffers(query: ListQuery): Promise<OfferListData> {
  const result = await callBackend(
    (client) => listOffers({ client, query, signal: timeoutSignal() }),
    backendOptions()
  );
  if (!result.success) fail('list_offers', result);
  return result.data;
}

async function loadCategories(): Promise<CategoryData[]> {
  const result = await callBackend(
    (client) => listCategories({ client, signal: timeoutSignal() }),
    backendOptions()
  );
  if (!result.success) fail('list_categories', result);
  return result.data;
}

/** Reads the 410 stub; null when the body is not the documented shape. */
function parseEnded(body: unknown): ExpiredOfferData | null {
  const data = (body as { data?: Partial<ExpiredOfferData> } | null | undefined)?.data;
  if (!data || typeof data.slug !== 'string' || typeof data.title !== 'string') return null;
  const category = data.category;
  if (!category || typeof category.slug !== 'string' || typeof category.name !== 'string') {
    return null;
  }
  const endedAt = typeof data.ended_at === 'string' ? data.ended_at : null;
  return { slug: data.slug, title: data.title, category, ended_at: endedAt };
}

/** The `code` the backend puts on its own 404 for an unknown or unpublished offer (never branch on the text). */
const OFFER_NOT_FOUND_CODE = 'offer_not_found';

type ErrorBody = { success?: unknown; code?: unknown; expired?: unknown };

function asErrorBody(body: unknown): ErrorBody | null {
  return typeof body === 'object' && body !== null ? (body as ErrorBody) : null;
}

/** The backend's own "no such offer": its error envelope WITH the offer_not_found code. */
function isOfferNotFound(body: unknown): boolean {
  const parsed = asErrorBody(body);
  return parsed?.success === false && parsed.code === OFFER_NOT_FOUND_CODE;
}

/** The backend's own "this offer has ended": its error envelope with `expired: true`. */
function isOfferEnded(body: unknown): boolean {
  const parsed = asErrorBody(body);
  return parsed?.success === false && parsed.expired === true;
}

async function loadOffer(slug: string): Promise<OfferReadResult> {
  // callBackend folds an error body into a message; the 404 and 410 bodies are the ones we need, so keep them.
  let errorBody: unknown;
  const result = await callBackend(async (client) => {
    const response = await readOffer({ client, path: { slug }, signal: timeoutSignal() });
    const status = response.response?.status;
    if (status === 404 || status === 410) errorBody = response.error;
    return response;
  }, backendOptions());
  if (result.success) return { kind: 'found', offer: result.data };
  // Only the backend's own answers are definitive, and pruning a saved offer is irreversible: a 404 or 410 from
  // anything else in the path (a proxy's HTML page, a wrong base URL, the backend's route-level "Not Found")
  // is an outage.
  if (result.status === 404 && isOfferNotFound(errorBody)) return { kind: 'not_found' };
  if (result.status === 410 && isOfferEnded(errorBody)) {
    return { kind: 'gone', ended: parseEnded(errorBody) };
  }
  return fail('read_offer', result);
}

const cacheOptions = { tags: [CATALOGUE_CACHE_TAG], revalidate: CATALOGUE_REVALIDATE_SECONDS };
const cachedOffers = unstable_cache(loadOffers, ['catalogue', 'offers'], cacheOptions);
const cachedCategories = unstable_cache(loadCategories, ['catalogue', 'categories'], cacheOptions);

/** A cached offer read with the time it was read, so a stale entry can be told from a fresh one. */
interface StampedOfferRead {
  at: number;
  result: OfferReadResult;
}

async function loadStampedOffer(slug: string): Promise<StampedOfferRead> {
  return { at: Date.now(), result: await loadOffer(slug) };
}

const cachedOffer = unstable_cache(loadStampedOffer, ['catalogue', 'offer'], cacheOptions);

/** A page of published, live offers plus the total before paging. */
export function fetchOffers(params: OfferListParams = {}): Promise<OfferListData> {
  const query = toQuery(params);
  return withVisitorIp(() => (query.q ? loadOffers(query) : cachedOffers(query)));
}

/** One offer: found, ended (410 stub) or not found. Any other outcome throws. */
export function fetchOffer(slug: string): Promise<OfferReadResult> {
  // A slug that is not a plain slug cannot name an offer: no round trip, no path injection.
  if (!isValidSlug(slug)) return Promise.resolve({ kind: 'not_found' });
  return withVisitorIp(async () => {
    const cached = await cachedOffer(slug);
    // `unstable_cache` serves a stale entry of any age while it refreshes in the background. The offer page says
    // whether a link earns commission, so a stale entry is not served: past the revalidate age the read goes
    // to the backend directly, bounding how old that disclosure can be.
    if (Date.now() - cached.at > CATALOGUE_REVALIDATE_SECONDS * 1000) return loadOffer(slug);
    return cached.result;
  });
}

/** Enabled categories in display order, each with its live offer count. */
export function fetchCategories(): Promise<CategoryData[]> {
  return withVisitorIp(() => cachedCategories());
}
