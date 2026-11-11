import { FRESHNESS_LABELS, OFFER_TYPE_LABELS } from '@/lib/catalogue/types';

/** Admin-facing labels for the API's upper-case enum values, reusing the public site's wording. */

/**
 * A label for `key`, or the key as sent when the table has none. Only the table's own entries count: a value such as
 * `constructor` or `__proto__` (from a tampered or corrupt row) is shown as text, never read from the prototype.
 */
export function labelFor(table: Readonly<Record<string, string>>, key: string): string {
  return Object.hasOwn(table, key) ? table[key] : key;
}

/** The label of an upper-case API value in a table keyed by its lower-case form; a value with none shows as sent. */
function lowerCaseLabel(table: Readonly<Record<string, string>>, value: string): string {
  const key = value.toLowerCase();
  return Object.hasOwn(table, key) ? table[key] : value;
}

export function offerTypeLabel(value: string): string {
  return lowerCaseLabel(OFFER_TYPE_LABELS, value);
}

export function freshnessLabel(value: string): string {
  return lowerCaseLabel(FRESHNESS_LABELS, value);
}

/** The labels of the offer form's fields that other admin views name too (the retailer update comparison). */
export const OFFER_FIELD_LABELS = {
  eligibility: 'Who can claim',
  requiresCard: 'Needs a payment card to claim',
  startAt: 'Starts (UTC)',
  endAt: 'Ends (UTC)',
} as const;

/** The three commercial bases, in the order the form lists them. Plain words, UK English. */
export const COMMERCIAL_BASIS_LABELS: Record<string, string> = {
  EDITORIAL: 'Editorial',
  AFFILIATE: 'Affiliate',
  SPONSORED: 'Sponsored',
};

/** One line under each choice. Sponsored is the only one visitors are told about. */
export const COMMERCIAL_BASIS_HELP: Record<string, string> = {
  EDITORIAL: 'Freejoy gets no payment or benefit for this listing.',
  AFFILIATE: 'Freejoy earns a commission through an affiliate network if someone takes it up.',
  SPONSORED:
    'The merchant pays Freejoy, or gives it a benefit, for the listing or where it appears. Visitors see a "Sponsored" label.',
};

export function commercialBasisLabel(value: string): string {
  return labelFor(COMMERCIAL_BASIS_LABELS, value);
}

export const POSTAGE_KIND_LABELS: Record<string, string> = {
  FREE: 'Free postage',
  COST: 'Postage costs money',
  CHECK_TERMS: 'Check the terms',
};

const PROVIDER_LABELS: Record<string, string> = { awin: 'Awin', partner: 'Retailer (partner API)' };

/** The affiliate network an offer was imported from, by name; one this UI does not know shows as sent. */
export function providerLabel(value: string): string {
  return labelFor(PROVIDER_LABELS, value);
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
  return labelFor(TINT_LABELS, value);
}
