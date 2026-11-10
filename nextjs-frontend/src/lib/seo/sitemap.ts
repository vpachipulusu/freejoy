/**
 * Sitemap entries. Pure: the caller passes the catalogue data and `today`.
 *
 * Rules: the home page, /freebies and /partners are always listed; an enabled category is listed only when
 * the catalogue is API-backed; an offer is listed only when it is indexable (API-sourced, not demo,
 * published and not expired). `lastModified` comes from the offer's own dates and is omitted when
 * there is none, never taken from the wall clock.
 */
import type { MetadataRoute } from 'next';
import type { CatalogueSource } from '@/lib/catalogue/source';
import type { Category, Offer } from '@/lib/catalogue/types';
import { isIndexableCategory, isIndexableOffer } from './indexing';
import { absoluteUrl } from './urls';

type SitemapEntry = MetadataRoute.Sitemap[number];

export interface SitemapInput {
  offers: readonly Offer[];
  categories: readonly Category[];
  today: string;
  source: CatalogueSource;
}

/** The date an offer was last touched by an editor, or its start date, or nothing. */
function offerLastModified(offer: Offer): string | undefined {
  return offer.lastVerifiedAt || offer.startAt || undefined;
}

function newest(dates: readonly (string | undefined)[]): string | undefined {
  const present = dates.filter((date): date is string => Boolean(date));
  return present.length === 0 ? undefined : present.reduce((a, b) => (a >= b ? a : b));
}

function entry(path: string, lastModified: string | undefined): SitemapEntry {
  return lastModified ? { url: absoluteUrl(path), lastModified } : { url: absoluteUrl(path) };
}

export function buildSitemapEntries({
  offers,
  categories,
  today,
  source,
}: SitemapInput): SitemapEntry[] {
  const indexable = offers.filter((offer) => isIndexableOffer(offer, today));
  const catalogueModified = newest(indexable.map(offerLastModified));

  const categoryEntries = categories
    .filter((category) => isIndexableCategory(category, source))
    .sort((a, b) => a.sortOrder - b.sortOrder)
    .map((category) =>
      entry(
        `/category/${category.slug}`,
        newest(
          indexable.filter((offer) => offer.categorySlug === category.slug).map(offerLastModified)
        )
      )
    );

  const offerEntries = indexable.map((offer) =>
    entry(`/freebies/${offer.slug}`, offerLastModified(offer))
  );

  return [
    entry('/', catalogueModified),
    entry('/freebies', catalogueModified),
    // Static page: always listed, and never dated from the catalogue.
    entry('/partners', undefined),
    ...categoryEntries,
    ...offerEntries,
  ];
}
