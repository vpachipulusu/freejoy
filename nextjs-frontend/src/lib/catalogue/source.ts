/**
 * Where the catalogue comes from. `CATALOGUE_SOURCE=api|fixture` picks it; unset means the API,
 * except under test (NODE_ENV=test) where it means the fixtures so unit tests stay offline.
 *
 * Read at call time and never exposed to the browser. SEO code asks `getCatalogueSource()` whether
 * the site is API-backed, independent of how many offers are live.
 */
import type { CatalogueQuery, Category, Merchant, Offer, Page } from './types';

export type CatalogueSource = 'fixture' | 'api';

/** What the facade needs from a data source. The fixture and API sources both implement it. */
export interface CatalogueBackend {
  getToday(): Promise<string>;
  getCategories(): Promise<Category[]>;
  getCategoryBySlug(slug: string): Promise<Category | null>;
  getMerchants(): Promise<Merchant[]>;
  getMerchantBySlug(slug: string): Promise<Merchant | null>;
  lookupOffer(slug: string): Promise<OfferLookup>;
  getLiveOffers(): Promise<Offer[]>;
  getFeaturedOffers(limit: number): Promise<Offer[]>;
  searchOffers(query: CatalogueQuery): Promise<Page<Offer>>;
  getRelatedOffers(offer: Offer, limit: number): Promise<Offer[]>;
}

/** The stub the API returns (HTTP 410) for an offer that was public and has ended. */
export interface EndedOffer {
  slug: string;
  title: string;
  categorySlug: string;
  categoryName: string;
  /** Europe/London calendar date (YYYY-MM-DD), or null when the editor retired it without an end date. */
  endedAt: string | null;
}

export type OfferLookup =
  | { kind: 'found'; offer: Offer }
  | { kind: 'ended'; ended: EndedOffer }
  | { kind: 'not_found' }
  /** The API answered, but the record is not one this site can read (an unknown value, a missing date). */
  | { kind: 'unreadable' };

export function getConfiguredSource(): CatalogueSource {
  const value = process.env.CATALOGUE_SOURCE?.trim().toLowerCase();
  if (value === 'fixture' || value === 'api') return value;
  return process.env.NODE_ENV === 'test' ? 'fixture' : 'api';
}
