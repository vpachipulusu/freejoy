/**
 * Freejoy catalogue contract.
 *
 * Fixtures (`fixtures.ts`, local design work and unit tests) and the catalogue API (`api-mapper.ts`) both
 * produce these shapes, so pages and components never know which one they are reading. Every value a page
 * shows as a trust cue (verification date, freshness label, postage, card requirement) is an
 * explicit field here: nothing is derived from the wall clock or invented at render time.
 */

/** The five offer kinds. A trial, voucher, cashback or competition is never described as a free sample. */
export type OfferType = 'free_sample' | 'free_trial' | 'cashback' | 'voucher' | 'competition';

export type OfferStatus = 'draft' | 'published' | 'expired' | 'archived';

/** What the visitor pays to receive the item. A £0 product may still carry postage. */
export type Postage =
  | { kind: 'free' }
  | { kind: 'cost'; amountMinor: number; currency: 'GBP' }
  | { kind: 'check_terms' };

/** Editorial freshness label set by the publisher, never computed from "now". */
export type Freshness = 'new_today' | 'just_added' | 'popular' | 'ends_soon' | 'limited';

/** Pastel tints used by the illustrated placeholders (design screens 01–07). */
export type Tint = 'blush' | 'mint' | 'lavender' | 'sky' | 'butter' | 'cream' | 'sage' | 'sand';

export interface Category {
  slug: string;
  name: string;
  /** Short label for chips and the mobile grid ("Home" for "Home & cleaning"). */
  shortName: string;
  /** One sentence for cards and metadata. */
  description: string;
  /** One or two sentences of useful intro copy for the category landing page. */
  intro: string;
  tint: Tint;
  sortOrder: number;
  enabled: boolean;
}

export interface Merchant {
  slug: string;
  name: string;
  /** Null until a licensed logo exists; the UI shows the name as text. */
  logoUrl: string | null;
}

export interface OfferImage {
  tint: Tint;
  /** Short text drawn inside the illustrated placeholder (e.g. "soft & kind"). */
  label: string;
  /** Alt text for the illustration. */
  alt: string;
}

export interface ClaimStep {
  title: string;
  detail: string;
}

export interface Offer {
  id: string;
  slug: string;
  title: string;
  /** One line under the title. */
  summary: string;
  /** Paragraphs of original editorial description. */
  description: string[];
  categorySlug: string;
  merchantSlug: string;
  /** Set on API offers, which carry their merchant; fixture offers resolve it from `merchantSlug`. */
  merchant?: Merchant;
  offerType: OfferType;
  status: OfferStatus;
  /**
   * True when the merchant pays or gives Freejoy a benefit for the listing or where it appears. Shown as a
   * "Sponsored" label on the card and the page. The editor's three-way basis is never public; only this flag is.
   */
  sponsored: boolean;
  /**
   * True when the offer earns Freejoy a commission (its confirmed basis is affiliate, or the link `/go/<id>`
   * follows is on an affiliate network); false when it does not; null when not known: a list item (the list
   * response does not carry the flag) or a detail body that omitted it. It gates the "Ad" label and the
   * commission sentence beside the outbound button; "Sponsored" is separate. Only the offer page reads it, and
   * `OfferHero` shows the disclosure unless it is exactly false.
   */
  earnsCommission: boolean | null;
  featured: boolean;
  image: OfferImage;
  postage: Postage;
  requiresCard: boolean;
  /** Present for trials and subscriptions: what the visitor commits to and how to cancel. */
  subscriptionTerms: string | null;
  eligibility: string;
  availability: string;
  /** ISO dates (YYYY-MM-DD). `endAt` null = no stated end date. */
  startAt: string;
  endAt: string | null;
  /** ISO date the editor last checked the offer. Shown as "Last checked". */
  lastVerifiedAt: string;
  freshness: Freshness | null;
  steps: ClaimStep[];
  /** True for fixture offers: the UI shows a demo badge and never links out. False for API offers. */
  demo: boolean;
  source: 'fixture' | 'api';
  /** Null for fixtures and API offers. Phase 3 resolves real destinations server-side via /go/[id]. */
  destinationUrl: string | null;
}

export type SortKey = 'newest' | 'ending_soon' | 'a_z';

/** Filter state carried in the catalogue URL. */
export interface CatalogueQuery {
  q: string;
  category: string | null;
  types: OfferType[];
  /** `'free'` = only offers with no postage. */
  postage: 'free' | null;
  sort: SortKey;
  page: number;
}

export interface Page<T> {
  items: T[];
  page: number;
  pageCount: number;
  total: number;
  /** 1-based inclusive bounds of `items` within `total`; both 0 when empty. */
  from: number;
  to: number;
}

export const OFFER_TYPES: readonly OfferType[] = [
  'free_sample',
  'free_trial',
  'cashback',
  'voucher',
  'competition',
] as const;

export const SORT_KEYS: readonly SortKey[] = ['newest', 'ending_soon', 'a_z'] as const;

export const OFFER_TYPE_LABELS: Record<OfferType, string> = {
  free_sample: 'Free sample',
  free_trial: 'Free trial',
  cashback: 'Cashback',
  voucher: 'Voucher',
  competition: 'Competition',
};

export const FRESHNESS_LABELS: Record<Freshness, string> = {
  new_today: 'New today',
  just_added: 'Just added',
  popular: 'Popular',
  ends_soon: 'Ends soon',
  limited: 'Limited',
};

export const SORT_LABELS: Record<SortKey, string> = {
  newest: 'Newest first',
  ending_soon: 'Ending soon',
  a_z: 'A to Z',
};

export const CATALOGUE_PAGE_SIZE = 12;

/** What an offer card reads. The Saved page sends only this much of an offer to the browser. */
export type CardOffer = Pick<
  Offer,
  | 'id'
  | 'slug'
  | 'title'
  | 'offerType'
  | 'sponsored'
  | 'image'
  | 'postage'
  | 'freshness'
  | 'demo'
  | 'source'
  | 'merchantSlug'
  | 'merchant'
>;

/** The card fields of an offer, copied so nothing else travels with them. */
export function toCardOffer(offer: Offer): CardOffer {
  return {
    id: offer.id,
    slug: offer.slug,
    title: offer.title,
    offerType: offer.offerType,
    sponsored: offer.sponsored,
    image: offer.image,
    postage: offer.postage,
    freshness: offer.freshness,
    demo: offer.demo,
    source: offer.source,
    merchantSlug: offer.merchantSlug,
    merchant: offer.merchant,
  };
}
