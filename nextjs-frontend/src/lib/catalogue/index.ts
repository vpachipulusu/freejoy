/**
 * Catalogue facade. Pages import from here only. Each function is async and answers from the
 * configured source: the catalogue API in production, fixtures for local design work and unit tests
 * (`CATALOGUE_SOURCE`, see `source.ts`). Reads that cannot reach the API throw
 * `CatalogueUnavailableError`; pages turn that into an error state with `settle`.
 */
import { apiSource } from './api-source';
import { fixtureSource } from './fixture-source';
import { getConfiguredSource, type CatalogueBackend, type CatalogueSource } from './source';
import type { CatalogueQuery, Category, Merchant, Offer, Page } from './types';

export * from './types';
export { DEFAULT_QUERY, parseCatalogueParams, toCatalogueHref, withQuery } from './offer-url';
export { isLive, normaliseQuery } from './query';
export {
  CATALOGUE_UNAVAILABLE_MESSAGE,
  CatalogueRateLimitedError,
  CatalogueUnavailableError,
  settle,
  type Loaded,
} from './errors';
export type { CatalogueSource, EndedOffer, OfferLookup } from './source';

function backend(): CatalogueBackend {
  return getConfiguredSource() === 'api' ? apiSource : fixtureSource;
}

/** Whether the site reads the catalogue API or the fixtures, independent of what is live right now. */
export async function getCatalogueSource(): Promise<CatalogueSource> {
  return getConfiguredSource();
}

/** Today's calendar date: the fixture date, or the Europe/London date when API-backed. */
export function getToday(): Promise<string> {
  return backend().getToday();
}

export function getCategories(): Promise<Category[]> {
  return backend().getCategories();
}

export function getCategoryBySlug(slug: string): Promise<Category | null> {
  return backend().getCategoryBySlug(slug);
}

/** Fixture merchants. API offers carry their merchant (`Offer.merchant`), so this is empty for the API. */
export function getMerchants(): Promise<Merchant[]> {
  return backend().getMerchants();
}

export function getMerchantBySlug(slug: string): Promise<Merchant | null> {
  return backend().getMerchantBySlug(slug);
}

/** The offer page's lookup: found, ended (the API's 410) or not found. */
export function lookupOffer(slug: string) {
  return backend().lookupOffer(slug);
}

export function getLiveOffers(): Promise<Offer[]> {
  return backend().getLiveOffers();
}

export function getFeaturedOffers(limit = 8): Promise<Offer[]> {
  return backend().getFeaturedOffers(limit);
}

export function searchOffers(query: CatalogueQuery): Promise<Page<Offer>> {
  return backend().searchOffers(query);
}

export function getRelatedOffers(offer: Offer, limit = 4): Promise<Offer[]> {
  return backend().getRelatedOffers(offer, limit);
}
