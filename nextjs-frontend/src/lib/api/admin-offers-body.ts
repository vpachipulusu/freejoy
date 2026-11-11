import 'server-only';

import {
  descriptionParagraphs,
  isCommercialBasis,
  poundsToMinor,
  type OfferFormValues,
} from '@/lib/admin-catalogue/offer-schema';
import { localInputToIso } from '@/lib/admin-catalogue/offer-view';
import type { CreateOfferRequest, UpdateOfferRequest } from './generated';

/** Postage is only ever priced in pounds in this UI. */
export const POSTAGE_CURRENCY = 'GBP';

function textOrNull(value: string): string | null {
  return value === '' ? null : value;
}

function dateOrNull(value: string): string | null {
  return value === '' ? null : localInputToIso(value);
}

/** The form schema refuses an empty choice, so this only fails for a caller that skipped validation. */
function requiredBasis(value: OfferFormValues['commercialBasis']) {
  if (!isCommercialBasis(value)) throw new Error('The commercial basis was not chosen');
  return value;
}

/** The editable fields, in the API's shape. Nullable fields are explicit nulls so an edit can clear them. */
function editableFields(values: OfferFormValues) {
  const costs = values.postageKind === 'COST';
  const minor = costs ? poundsToMinor(values.postageAmount) : null;
  return {
    title: values.title,
    summary: values.summary,
    description: descriptionParagraphs(values.description),
    category_id: values.categoryId,
    merchant_id: values.merchantId,
    offer_type: values.offerType,
    featured: values.featured,
    image_tint: values.imageTint,
    image_label: values.imageLabel,
    image_alt: values.imageAlt,
    source_url: textOrNull(values.sourceUrl),
    postage_kind: values.postageKind,
    postage_amount_minor: minor,
    postage_currency: costs ? POSTAGE_CURRENCY : null,
    requires_card: values.requiresCard,
    subscription_terms: textOrNull(values.subscriptionTerms),
    eligibility: values.eligibility,
    availability: values.availability,
    steps: values.steps.map(({ title, detail }) => ({ title, detail })),
    start_at: dateOrNull(values.startAt),
    end_at: dateOrNull(values.endAt),
    freshness: values.freshness === '' ? null : values.freshness,
    publisher_notes: textOrNull(values.publisherNotes),
  };
}

/** A new offer. The slug is sent only when the admin typed one; otherwise the API derives it from the title. */
export function toCreateOfferBody(values: OfferFormValues): CreateOfferRequest {
  return {
    ...editableFields(values),
    commercial_basis: requiredBasis(values.commercialBasis),
    ...(values.slug === '' ? {} : { slug: values.slug }),
  };
}

/**
 * An edit sends every editable field. The slug and status are not editable and are never sent. The review token
 * of the version the form shows goes with it: the API needs it when the commercial basis changes, so a form that
 * was loaded before somebody else changed the basis cannot undo that change. A basis nobody has chosen (a
 * retailer's submission whose placeholder the form shows as not chosen) is omitted when `basisOptional` is set.
 */
export function toUpdateOfferBody(
  values: OfferFormValues,
  reviewToken?: string,
  options: { basisOptional?: boolean } = {}
): UpdateOfferRequest {
  // An unchosen basis is only ever left out on purpose (`basisOptional`): the stored one then stays as it is.
  const basis =
    options.basisOptional && values.commercialBasis === ''
      ? {}
      : { commercial_basis: requiredBasis(values.commercialBasis) };
  return {
    ...editableFields(values),
    ...basis,
    ...(reviewToken ? { review_token: reviewToken } : {}),
  };
}
