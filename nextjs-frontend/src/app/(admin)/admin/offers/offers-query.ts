import { ADMIN_OFFERS_PAGE_SIZE, MAX_OFFSET } from '@/lib/admin-catalogue/paging';
import { OFFER_STATUSES, type OfferStatus } from '@/lib/admin-catalogue/offer-status';

/** The list page's URL parameters: parsing, and the links that carry them (chips, pagination, retry). */

export const OFFERS_PATH = '/admin/offers';
export const MAX_QUERY_LENGTH = 100;
/** The one value of `review` that means something: `?review=needed` is the review queue. */
export const REVIEW_NEEDED = 'needed';

/** The API refuses an offset past 1,000,000, so a later page is the last one that can exist. */
const MAX_PAGE = Math.floor(MAX_OFFSET / ADMIN_OFFERS_PAGE_SIZE) + 1;
const ID_PATTERN = /^[A-Za-z0-9_-]{1,128}$/;

export type OffersQuery = {
  q: string | undefined;
  status: OfferStatus | undefined;
  category: string | undefined;
  /** True for `?review=needed`: imported offers that are drafts or whose source changed. */
  review: boolean;
  page: number;
};

type RawParams = {
  q?: string;
  status?: string;
  category?: string;
  review?: string;
  page?: string;
};

function parsePage(value: string | undefined): number {
  const page = Number.parseInt(value ?? '1', 10);
  return Number.isFinite(page) && page > 0 ? Math.min(page, MAX_PAGE) : 1;
}

function parseStatus(value: string | undefined): OfferStatus | undefined {
  const upper = value?.toUpperCase();
  return OFFER_STATUSES.find((status) => status === upper);
}

export function parseOffersQuery(params: RawParams): OffersQuery {
  return {
    q: params.q?.trim().slice(0, MAX_QUERY_LENGTH) || undefined,
    status: parseStatus(params.status),
    category: params.category && ID_PATTERN.test(params.category) ? params.category : undefined,
    review: params.review === REVIEW_NEEDED,
    page: parsePage(params.page),
  };
}

export function offersHref(query: Partial<OffersQuery>): string {
  const params = new URLSearchParams();
  if (query.q) params.set('q', query.q);
  if (query.status) params.set('status', query.status.toLowerCase());
  if (query.category) params.set('category', query.category);
  if (query.review) params.set('review', REVIEW_NEEDED);
  if (query.page && query.page > 1) params.set('page', String(query.page));
  const qs = params.toString();
  return qs ? `${OFFERS_PATH}?${qs}` : OFFERS_PATH;
}
