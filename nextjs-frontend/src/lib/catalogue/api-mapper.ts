/**
 * Maps catalogue API records onto the `Offer` / `Category` shapes the pages render. Pure apart from
 * logging a dropped record. This is the facade boundary: nothing the API sends reaches a page or a URL
 * before it has passed here.
 */
import type { ApiCategory, ApiEndedOffer, ApiOffer } from '@/lib/api/catalogue';
import { logger } from '@/lib/logger';
import { CatalogueUnavailableError } from './errors';
import { isValidSlug } from './slug';
import { toLondonDate } from './today';
import type { EndedOffer } from './source';
import {
  OFFER_TYPES,
  type Category,
  type ClaimStep,
  type Merchant,
  type Offer,
  type OfferImage,
  type Postage,
  type Tint,
} from './types';

const TINTS: readonly Tint[] = [
  'blush',
  'mint',
  'lavender',
  'sky',
  'butter',
  'cream',
  'sage',
  'sand',
];
const FALLBACK_TINT: Tint = 'cream';
const FRESHNESS_LABELS: readonly string[] = [
  'new_today',
  'just_added',
  'popular',
  'ends_soon',
  'limited',
];
const MAX_LOGGED_VALUE = 60;

/** Logs why a record was dropped. The value is JSON-quoted and cut short so it cannot forge log lines. */
function drop(kind: string, field: string, value: unknown): null {
  logger.warn('Catalogue: dropped an API record', {
    kind,
    field,
    value: JSON.stringify(value)?.slice(0, MAX_LOGGED_VALUE),
  });
  return null;
}

function toTint(value: string): Tint {
  return (TINTS as readonly string[]).includes(value) ? (value as Tint) : FALLBACK_TINT;
}

function toPostage(api: ApiOffer['postage']): Postage {
  if (api.kind === 'free') return { kind: 'free' };
  if (api.kind === 'cost' && typeof api.amount_minor === 'number' && api.currency === 'GBP') {
    return { kind: 'cost', amountMinor: api.amount_minor, currency: 'GBP' };
  }
  return { kind: 'check_terms' };
}

function toImage(api: ApiOffer['image']): OfferImage {
  return { tint: toTint(api.tint), label: api.label, alt: api.alt };
}

/** A merchant logo is shown as an image source, so only a plain https URL (no credentials) is accepted. */
export function safeLogoUrl(value: string | null): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && !url.username && !url.password ? value : null;
  } catch {
    return null;
  }
}

function toMerchant(api: ApiOffer['merchant']): Merchant {
  return { slug: api.slug, name: api.name, logoUrl: safeLogoUrl(api.logo_url) };
}

type Obj = Record<string, unknown>;
const isObj = (value: unknown): value is Obj =>
  typeof value === 'object' && value !== null && !Array.isArray(value);
const allStrings = (obj: Obj, fields: readonly string[]) =>
  fields.every((field) => typeof obj[field] === 'string');

/**
 * The first field of an API offer that does not have the documented shape, or null. The generated types
 * promise the shape; this guards the boundary at runtime so one malformed record is dropped and logged
 * instead of throwing a TypeError that takes the whole page down.
 */
function malformedOfferField(api: unknown): string | null {
  if (!isObj(api)) return 'record';
  const text = ['id', 'slug', 'title', 'summary', 'eligibility', 'availability', 'start_at'];
  const badText = text.find((field) => typeof api[field] !== 'string');
  if (badText) return badText;
  const objects = ['category', 'merchant', 'image', 'postage'];
  const badObject = objects.find((field) => !isObj(api[field]));
  if (badObject) return badObject;
  const { category, merchant, image, postage } = api as { [key: string]: Obj };
  if (!allStrings(category, ['slug', 'name'])) return 'category';
  if (!allStrings(merchant, ['slug', 'name'])) return 'merchant';
  if (!allStrings(image, ['tint', 'label', 'alt'])) return 'image';
  if (typeof postage.kind !== 'string') return 'postage';
  if (!Array.isArray(api.description) || !api.description.every((p) => typeof p === 'string')) {
    return 'description';
  }
  const stepsOk =
    Array.isArray(api.steps) &&
    api.steps.every((s) => isObj(s) && allStrings(s, ['title', 'detail']));
  if (!stepsOk) return 'steps';
  // `sponsored` is a legal label: a record that does not say is dropped, never shown as if it were not sponsored.
  const flags = [api.featured, api.requires_card, api.sponsored];
  if (!flags.every((flag) => typeof flag === 'boolean')) return 'flags';
  const terms = api.subscription_terms;
  if (terms !== null && typeof terms !== 'string') return 'subscription_terms';
  const label = api.freshness;
  if (label !== null && !(typeof label === 'string' && FRESHNESS_LABELS.includes(label))) {
    return 'freshness';
  }
  return null;
}

function toSteps(steps: ApiOffer['steps']): ClaimStep[] {
  return steps.map((step) => ({ title: step.title, detail: step.detail }));
}

/** The first invalid slug on an offer (field name and value), or null when all three are fine. */
function invalidSlug(api: ApiOffer): { field: string; value: unknown } | null {
  if (!isValidSlug(api.slug)) return { field: 'slug', value: api.slug };
  if (!isValidSlug(api.category.slug)) return { field: 'category.slug', value: api.category.slug };
  if (!isValidSlug(api.merchant.slug)) return { field: 'merchant.slug', value: api.merchant.slug };
  return null;
}

/**
 * One API offer as an `Offer`, or null (logged) when it cannot be shown honestly: an unsafe slug, an
 * unreadable date, or no verification date (the page says "Last checked", so it never invents one).
 */
export function mapOffer(api: ApiOffer): Offer | null {
  const malformed = malformedOfferField(api);
  if (malformed) return drop('offer', malformed, null);
  const badSlug = invalidSlug(api);
  if (badSlug) return drop('offer', badSlug.field, badSlug.value);

  const startAt = toLondonDate(api.start_at);
  const endAt = api.end_at === null ? null : toLondonDate(api.end_at);
  const lastVerifiedAt = api.last_verified_at === null ? null : toLondonDate(api.last_verified_at);
  if (startAt === null) return drop('offer', 'start_at', api.start_at);
  if (api.end_at !== null && endAt === null) return drop('offer', 'end_at', api.end_at);
  if (lastVerifiedAt === null) return drop('offer', 'last_verified_at', api.last_verified_at);
  if (!OFFER_TYPES.includes(api.offer_type)) return drop('offer', 'offer_type', api.offer_type);

  return {
    id: api.id,
    slug: api.slug,
    title: api.title,
    summary: api.summary,
    description: [...api.description],
    categorySlug: api.category.slug,
    merchantSlug: api.merchant.slug,
    merchant: toMerchant(api.merchant),
    offerType: api.offer_type,
    status: 'published',
    sponsored: api.sponsored,
    featured: api.featured,
    image: toImage(api.image),
    postage: toPostage(api.postage),
    requiresCard: api.requires_card,
    subscriptionTerms: api.subscription_terms,
    eligibility: api.eligibility,
    availability: api.availability,
    startAt,
    endAt,
    lastVerifiedAt,
    freshness: api.freshness ?? null,
    steps: toSteps(api.steps),
    demo: false,
    source: 'api',
    destinationUrl: null,
  };
}

export function mapOffers(items: readonly ApiOffer[]): Offer[] {
  const offers = items.flatMap((item) => mapOffer(item) ?? []);
  // A whole non-empty page that cannot be read is this site being out of step with the API (a field it needs is
  // missing), not an empty catalogue: say so instead of showing "no freebies". One bad record among good ones is
  // still just dropped.
  if (items.length > 0 && offers.length === 0) {
    logger.warn('Catalogue: every offer on a page was dropped', { count: items.length });
    throw new CatalogueUnavailableError();
  }
  return offers;
}

/** One API category as a `Category`, or null (logged) for an unsafe slug. */
export function mapCategory(api: ApiCategory): Category | null {
  const fields = ['slug', 'name', 'short_name', 'description', 'intro', 'tint'];
  if (
    !isObj(api) ||
    !allStrings(api as unknown as Obj, fields) ||
    typeof api.sort_order !== 'number'
  ) {
    return drop('category', 'record', null);
  }
  if (!isValidSlug(api.slug)) return drop('category', 'slug', api.slug);
  return {
    slug: api.slug,
    name: api.name,
    shortName: api.short_name,
    description: api.description,
    intro: api.intro,
    tint: toTint(api.tint),
    sortOrder: api.sort_order,
    enabled: true,
  };
}

/** The 410 stub as an `EndedOffer`, or null (logged) when its slugs are unsafe. */
export function mapEndedOffer(api: ApiEndedOffer): EndedOffer | null {
  const shaped =
    isObj(api) && allStrings(api as unknown as Obj, ['slug', 'title']) && isObj(api.category);
  if (!shaped || !allStrings(api.category as unknown as Obj, ['slug', 'name'])) {
    return drop('ended offer', 'record', null);
  }
  if (!isValidSlug(api.slug)) return drop('ended offer', 'slug', api.slug);
  if (!isValidSlug(api.category.slug))
    return drop('ended offer', 'category.slug', api.category.slug);
  return {
    slug: api.slug,
    title: api.title,
    categorySlug: api.category.slug,
    categoryName: api.category.name,
    endedAt: api.ended_at === null ? null : toLondonDate(api.ended_at),
  };
}
