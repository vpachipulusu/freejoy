import 'server-only';

/**
 * API-backed catalogue. Reads go through the server-only wrapper (`@/lib/api/catalogue`, cached for a
 * minute) and every record passes `api-mapper.ts` before a page sees it. A failed read throws
 * `CatalogueUnavailableError`; nothing here substitutes an empty list for an outage.
 *
 * Reads that several parts of one render repeat (metadata, heading and results all ask for the same
 * page) are memoised per request with React `cache`, so each costs one lookup.
 */
import { cache } from 'react';
import {
  fetchCategories,
  fetchOffer,
  fetchOffers,
  type OfferListParams,
} from '@/lib/api/catalogue';
import { mapCategory, mapEndedOffer, mapOffer, mapOffers } from './api-mapper';
import { normaliseQuery } from './query';
import { isValidSlug } from './slug';
import type { CatalogueBackend, OfferLookup } from './source';
import { londonDate } from './today';
import {
  CATALOGUE_PAGE_SIZE,
  type CatalogueQuery,
  type Category,
  type Offer,
  type Page,
} from './types';

/** The API caps `offset` at 10000, so the deepest page a visitor can reach. */
const MAX_OFFSET = 10_000;
const MAX_PAGE = Math.floor(MAX_OFFSET / CATALOGUE_PAGE_SIZE) + 1;
const API_MAX_LIMIT = 50;
/** Safety cap when walking the whole live list (sitemap): 40 requests, 2000 offers. */
const MAX_LIVE_OFFERS = 2_000;
const RELATED_PAD_FETCH = 12;

const loadCategories = cache(async (): Promise<Category[]> => {
  const items = await fetchCategories();
  return items.flatMap((item) => mapCategory(item) ?? []).sort((a, b) => a.sortOrder - b.sortOrder);
});

function paramsFor(query: CatalogueQuery, page: number): OfferListParams {
  const q = normaliseQuery(query.q);
  return {
    ...(q !== '' ? { q } : {}),
    ...(query.category !== null ? { category: query.category } : {}),
    types: query.types,
    ...(query.postage !== null ? { postage: query.postage } : {}),
    sort: query.sort,
    limit: CATALOGUE_PAGE_SIZE,
    offset: (page - 1) * CATALOGUE_PAGE_SIZE,
  };
}

function emptyPage(): Page<Offer> {
  return { items: [], page: 1, pageCount: 1, total: 0, from: 0, to: 0 };
}

function toPage(items: Offer[], total: number, page: number): Page<Offer> {
  const offset = (page - 1) * CATALOGUE_PAGE_SIZE;
  return {
    items,
    page,
    pageCount: Math.max(1, Math.ceil(total / CATALOGUE_PAGE_SIZE)),
    total,
    from: items.length === 0 ? 0 : offset + 1,
    to: items.length === 0 ? 0 : offset + items.length,
  };
}

async function readPage(query: CatalogueQuery, page: number): Promise<Page<Offer>> {
  const data = await fetchOffers(paramsFor(query, page));
  return toPage(mapOffers(data.items), data.total, page);
}

async function runSearch(query: CatalogueQuery): Promise<Page<Offer>> {
  if (query.category !== null && !isValidSlug(query.category)) return emptyPage();
  const requested = Math.min(Math.max(1, Math.floor(query.page)), MAX_PAGE);
  const first = await readPage(query, requested);
  if (first.items.length > 0 || first.total === 0 || requested === 1) return first;
  // A page past the end: show the last page instead, as the fixture pagination does.
  return readPage(query, Math.min(first.pageCount, MAX_PAGE));
}

/** Keyed by the serialised query so equal queries within one request share one lookup. */
const searchOnce = cache((key: string) => runSearch(JSON.parse(key) as CatalogueQuery));

async function searchOffers(query: CatalogueQuery): Promise<Page<Offer>> {
  return searchOnce(JSON.stringify(query));
}

async function getLiveOffers(): Promise<Offer[]> {
  const offers: Offer[] = [];
  for (let offset = 0; offset < MAX_LIVE_OFFERS; offset += API_MAX_LIMIT) {
    const data = await fetchOffers({ sort: 'newest', limit: API_MAX_LIMIT, offset });
    offers.push(...mapOffers(data.items));
    if (offset + API_MAX_LIMIT >= data.total) break;
  }
  return offers;
}

async function getFeaturedOffers(limit: number): Promise<Offer[]> {
  const count = Math.min(Math.floor(limit), API_MAX_LIMIT);
  if (count <= 0) return [];
  const data = await fetchOffers({ featured: true, sort: 'newest', limit: count, offset: 0 });
  return mapOffers(data.items);
}

/** Live offers from the same category (newest first), padded with other featured offers. */
async function getRelatedOffers(offer: Offer, limit: number): Promise<Offer[]> {
  if (limit <= 0) return [];
  const sameCategory = await fetchOffers({ category: offer.categorySlug, limit: limit + 1 });
  const related = mapOffers(sameCategory.items).filter((entry) => entry.id !== offer.id);
  if (related.length >= limit) return related.slice(0, limit);

  const featured = await fetchOffers({ featured: true, limit: RELATED_PAD_FETCH });
  const padding = mapOffers(featured.items).filter(
    (entry) => entry.id !== offer.id && entry.categorySlug !== offer.categorySlug
  );
  return [...related, ...padding].slice(0, limit);
}

const lookupOnce = cache(async (slug: string): Promise<OfferLookup> => {
  const result = await fetchOffer(slug);
  if (result.kind === 'not_found') return { kind: 'not_found' };
  if (result.kind === 'found') {
    const offer = mapOffer(result.offer);
    // Not "not found": the API has the offer, this site just cannot read it (yet). Callers must not discard it.
    return offer ? { kind: 'found', offer } : { kind: 'unreadable' };
  }
  const ended = result.ended ? mapEndedOffer(result.ended) : null;
  return ended ? { kind: 'ended', ended } : { kind: 'not_found' };
});

async function lookupOffer(slug: string): Promise<OfferLookup> {
  // A slug that is not a plain slug cannot name an offer; skip the round trip.
  return isValidSlug(slug) ? lookupOnce(slug) : { kind: 'not_found' };
}

export const apiSource: CatalogueBackend = {
  async getToday() {
    return londonDate(new Date());
  },
  getCategories: loadCategories,
  async getCategoryBySlug(slug) {
    if (!isValidSlug(slug)) return null;
    return (await loadCategories()).find((category) => category.slug === slug) ?? null;
  },
  // The API sends each offer's merchant with it (`Offer.merchant`); there is no merchant list.
  async getMerchants() {
    return [];
  },
  async getMerchantBySlug() {
    return null;
  },
  lookupOffer,
  getLiveOffers,
  getFeaturedOffers,
  searchOffers,
  getRelatedOffers,
};
