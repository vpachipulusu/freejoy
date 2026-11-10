import {
  FRESHNESS_LABELS,
  OFFER_TYPE_LABELS,
  type Freshness,
  type OfferType,
} from '@/lib/catalogue/types';

/** Admin-facing labels for the API's upper-case enum values, reusing the public site's wording. */

export function offerTypeLabel(value: string): string {
  return OFFER_TYPE_LABELS[value.toLowerCase() as OfferType] ?? value;
}

export function freshnessLabel(value: string): string {
  return FRESHNESS_LABELS[value.toLowerCase() as Freshness] ?? value;
}

export const POSTAGE_KIND_LABELS: Record<string, string> = {
  FREE: 'Free postage',
  COST: 'Postage costs money',
  CHECK_TERMS: 'Check the terms',
};

const PROVIDER_LABELS: Record<string, string> = { awin: 'Awin' };

/** The affiliate network an offer was imported from, by name; one this UI does not know shows as sent. */
export function providerLabel(value: string): string {
  return PROVIDER_LABELS[value] ?? value;
}

const TINT_LABELS: Record<string, string> = {
  blush: 'Blush',
  mint: 'Mint',
  lavender: 'Lavender',
  sky: 'Sky',
  butter: 'Butter',
  cream: 'Cream',
  sage: 'Sage',
  sand: 'Sand',
};

export function tintLabel(value: string): string {
  return TINT_LABELS[value] ?? value;
}
