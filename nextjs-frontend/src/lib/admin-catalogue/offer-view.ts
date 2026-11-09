import { minorToPounds, type OfferFormValues } from './offer-schema';
import type { OfferRowStatus } from './offer-status';

/**
 * Browser-facing shapes of admin offers, in the camelCase the admin UI uses. They carry only what the pages
 * render; `toOfferView` in src/lib/api/admin-offers.ts builds them from the API response.
 */

export interface OfferRowView {
  id: string;
  title: string;
  slug: string;
  categoryId: string;
  merchantId: string;
  offerType: string;
  status: OfferRowStatus;
  featured: boolean;
  endAt: string | null;
  lastVerifiedAt: string | null;
  updatedAt: string;
  /** Opaque: sent back with publish and verify, which fail if the offer changed since it was loaded. */
  reviewToken: string;
}

export interface OfferLinkView {
  id: string;
  provider: string;
  destinationUrl: string;
  active: boolean;
}

export interface OfferDetailView extends OfferRowView {
  summary: string;
  description: string[];
  imageTint: string;
  imageLabel: string;
  imageAlt: string;
  sourceUrl: string | null;
  postageKind: string;
  postageAmountMinor: number | null;
  postageCurrency: string | null;
  requiresCard: boolean;
  subscriptionTerms: string | null;
  eligibility: string;
  availability: string;
  steps: { title: string; detail: string }[];
  startAt: string | null;
  freshness: string | null;
  publisherNotes: string | null;
  createdAt: string;
  links: OfferLinkView[];
}

const LOCAL_DATE_TIME_LENGTH = 19;

/** An ISO timestamp as the value of a `datetime-local` input, read as UTC ("2026-03-01T09:30:00"). */
export function isoToLocalInput(value: string | null): string {
  if (!value || Number.isNaN(Date.parse(value))) return '';
  return new Date(value).toISOString().slice(0, LOCAL_DATE_TIME_LENGTH);
}

/** A `datetime-local` value, read as UTC, as an ISO timestamp with a zone. */
export function localInputToIso(value: string): string {
  return value.length === 16 ? `${value}:00Z` : `${value}Z`;
}

/** The form's starting values for an existing offer. Unknown enum values fall back to the form defaults. */
export function offerToFormValues(offer: OfferDetailView): OfferFormValues {
  const postageIsCost = offer.postageKind === 'COST' && offer.postageAmountMinor !== null;
  return {
    title: offer.title,
    slug: offer.slug,
    summary: offer.summary,
    description: offer.description.join('\n\n'),
    categoryId: offer.categoryId,
    merchantId: offer.merchantId,
    offerType: offer.offerType as OfferFormValues['offerType'],
    featured: offer.featured,
    imageTint: offer.imageTint as OfferFormValues['imageTint'],
    imageLabel: offer.imageLabel,
    imageAlt: offer.imageAlt,
    sourceUrl: offer.sourceUrl ?? '',
    postageKind: offer.postageKind as OfferFormValues['postageKind'],
    postageAmount: postageIsCost ? minorToPounds(offer.postageAmountMinor as number) : '',
    requiresCard: offer.requiresCard,
    subscriptionTerms: offer.subscriptionTerms ?? '',
    eligibility: offer.eligibility,
    availability: offer.availability,
    steps: offer.steps,
    startAt: isoToLocalInput(offer.startAt),
    endAt: isoToLocalInput(offer.endAt),
    freshness: (offer.freshness ?? '') as OfferFormValues['freshness'],
    publisherNotes: offer.publisherNotes ?? '',
  };
}
