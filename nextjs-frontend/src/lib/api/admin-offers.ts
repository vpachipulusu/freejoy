import 'server-only';

import {
  isOfferStatus,
  OFFER_STATUSES,
  type OfferAction,
  type OfferRowStatus,
  type ReviewedOfferAction,
  type OfferStatus,
} from '@/lib/admin-catalogue/offer-status';
import type {
  OfferDetailView,
  OfferLinkView,
  OfferProvenanceView,
  OfferRowView,
} from '@/lib/admin-catalogue/offer-view';
import { ADMIN_OFFERS_PAGE_SIZE } from '@/lib/admin-catalogue/paging';
import { callBackend, type ApiResult } from './server';
import {
  adminArchiveOffer,
  adminCreateOffer,
  adminDismissOfferSourceChange,
  adminExpireOffer,
  adminListOffers,
  adminPublishOffer,
  adminReadOffer,
  adminUnpublishOffer,
  adminUpdateOffer,
  adminVerifyOffer,
  type AffiliateLinkOut,
  type CreateOfferRequest,
  type OfferOut,
  type OfferProvenanceOut,
  type OfferSummaryOut,
  type UpdateOfferRequest,
} from './generated';

/**
 * Server-only calls to the admin offers API (`/v1/admin/offers`) for the admin pages and server actions.
 * Callers import these instead of the generated SDK (ADR-008). The backend authenticates the session, checks the
 * admin's permission on every call (`offers.read`, `offers.write`, `offers.publish`) and writes the audit log.
 */

export { ADMIN_OFFERS_PAGE_SIZE };
const MAX_SEARCH_LENGTH = 100;
const COUNT_ONLY_LIMIT = 1;

export interface OfferListParams {
  search?: string;
  status?: OfferStatus;
  categoryId?: string;
  /** Only the review queue: imported offers that are drafts or whose source changed (see the backend docs). */
  needsReview?: boolean;
  limit?: number;
  offset?: number;
}

export function fetchAdminOffers(params: OfferListParams) {
  const search = params.search?.trim().slice(0, MAX_SEARCH_LENGTH);
  return callBackend((client) =>
    adminListOffers({
      client,
      query: {
        limit: params.limit ?? ADMIN_OFFERS_PAGE_SIZE,
        offset: params.offset ?? 0,
        ...(search ? { search } : {}),
        ...(params.status ? { status: params.status } : {}),
        ...(params.categoryId ? { category_id: params.categoryId } : {}),
        ...(params.needsReview ? { needs_review: true } : {}),
      },
    })
  );
}

export function fetchAdminOffer(offerId: string) {
  return callBackend((client) => adminReadOffer({ client, path: { offer_id: offerId } }));
}

export function createAdminOffer(body: CreateOfferRequest) {
  return callBackend((client) => adminCreateOffer({ client, body }));
}

export function patchAdminOffer(offerId: string, body: UpdateOfferRequest) {
  return callBackend((client) => adminUpdateOffer({ client, path: { offer_id: offerId }, body }));
}

/**
 * A lifecycle call. Publish and verify say "a person reviewed this", so they carry the `review_token` of the
 * offer the admin loaded; the backend answers 409 `offer_changed` when the offer or its links moved since. The
 * steps that only take an offer off the public site carry nothing.
 */
export type OfferActionRequest =
  | { action: ReviewedOfferAction; reviewToken: string }
  | { action: Exclude<OfferAction, ReviewedOfferAction> };

/** The lifecycle calls. (The API's optional verify note is not used by this UI.) */
export function runAdminOfferAction(offerId: string, request: OfferActionRequest) {
  const path = { offer_id: offerId };
  return callBackend((client) => {
    switch (request.action) {
      case 'publish':
        return adminPublishOffer({ client, path, body: { review_token: request.reviewToken } });
      case 'verify':
        return adminVerifyOffer({ client, path, body: { review_token: request.reviewToken } });
      case 'unpublish':
        return adminUnpublishOffer({ client, path });
      case 'expire':
        return adminExpireOffer({ client, path });
      case 'archive':
        return adminArchiveOffer({ client, path });
    }
  });
}

/**
 * Dismiss the "source changed" marker of an imported offer. It says "a person has seen the change" and sends the
 * `review_token` of the offer the admin loaded; the backend answers 409 `offer_changed` when it moved since, and
 * 409 `illegal_transition` when there is nothing to dismiss.
 */
export function dismissAdminOfferSourceChange(offerId: string, reviewToken: string) {
  return callBackend((client) =>
    adminDismissOfferSourceChange({
      client,
      path: { offer_id: offerId },
      body: { review_token: reviewToken },
    })
  );
}

export type OfferStatusCounts = Record<OfferStatus | 'TOTAL' | 'NEEDS_REVIEW', number>;

/**
 * Offer counts for the stats strip, read from the list endpoint's `total_count` (a one-row page per status, and
 * one more for the review queue). The total is the sum of the four statuses, so the strip is always consistent
 * with itself; the review queue overlaps the statuses and is never added in. Nothing is estimated: if any call
 * fails the whole read fails and the strip says so.
 */
export async function fetchOfferStatusCounts(): Promise<ApiResult<OfferStatusCounts>> {
  const [queue, ...results] = await Promise.all([
    fetchAdminOffers({ needsReview: true, limit: COUNT_ONLY_LIMIT }),
    ...OFFER_STATUSES.map((status) => fetchAdminOffers({ status, limit: COUNT_ONLY_LIMIT })),
  ]);
  if (!queue.success) return queue;
  const counts: Partial<OfferStatusCounts> = {};
  let total = 0;
  for (const [index, result] of results.entries()) {
    if (!result.success) return result;
    counts[OFFER_STATUSES[index]] = result.data.total_count;
    total += result.data.total_count;
  }
  return {
    success: true,
    data: {
      ...(counts as Record<OfferStatus, number>),
      TOTAL: total,
      NEEDS_REVIEW: queue.data.total_count,
    },
  };
}

/** A status this UI does not know is kept as UNKNOWN: shown plainly and never offered an action. */
function readStatus(value: string): OfferRowStatus {
  return isOfferStatus(value) ? value : 'UNKNOWN';
}

export function toOfferRow(offer: OfferSummaryOut): OfferRowView {
  return {
    id: offer.id,
    title: offer.title,
    slug: offer.slug,
    categoryId: offer.category_id,
    merchantId: offer.merchant_id,
    offerType: offer.offer_type,
    status: readStatus(offer.status),
    featured: offer.featured,
    endAt: offer.end_at,
    lastVerifiedAt: offer.last_verified_at,
    updatedAt: offer.updated_at,
    importProvider: offer.import_provider,
    sourceChangedAt: offer.source_changed_at,
  };
}

function toProvenance(provenance: OfferProvenanceOut | null): OfferProvenanceView | null {
  if (!provenance) return null;
  return {
    provider: provenance.provider,
    externalId: provenance.external_id,
    appliedAt: provenance.applied_at,
    sourceChangedAt: provenance.source_changed_at,
  };
}

function toOfferLink(link: AffiliateLinkOut): OfferLinkView {
  return {
    id: link.id,
    provider: link.provider,
    destinationUrl: link.destination_url,
    active: link.active,
  };
}

function toStepView(step: Record<string, unknown>) {
  return {
    title: typeof step.title === 'string' ? step.title : '',
    detail: typeof step.detail === 'string' ? step.detail : '',
  };
}

export function toOfferDetail(offer: OfferOut): OfferDetailView {
  return {
    ...toOfferRow(offer),
    reviewToken: offer.review_token,
    summary: offer.summary,
    description: offer.description,
    imageTint: offer.image_tint,
    imageLabel: offer.image_label,
    imageAlt: offer.image_alt,
    sourceUrl: offer.source_url,
    postageKind: offer.postage_kind,
    postageAmountMinor: offer.postage_amount_minor,
    postageCurrency: offer.postage_currency,
    requiresCard: offer.requires_card,
    subscriptionTerms: offer.subscription_terms,
    eligibility: offer.eligibility,
    availability: offer.availability,
    steps: offer.steps.map(toStepView),
    startAt: offer.start_at,
    freshness: offer.freshness,
    publisherNotes: offer.publisher_notes,
    createdAt: offer.created_at,
    links: offer.links.map(toOfferLink),
    provenance: toProvenance(offer.provenance),
  };
}
