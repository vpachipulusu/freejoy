'use server';

import { revalidatePath } from 'next/cache';
import { logger } from '@/lib/logger';
import {
  isCommercialBasis,
  offerEditSchema,
  offerFormSchema,
} from '@/lib/admin-catalogue/offer-schema';
import {
  isOfferAction,
  isReviewedOfferAction,
  type OfferAction,
  type OfferStatus,
} from '@/lib/admin-catalogue/offer-status';
import {
  asAdmin,
  failed,
  invalidForm,
  notFound,
  pendingHashSchema,
  refreshPublicCatalogue,
  resourceIdSchema,
  reviewTokenSchema,
  sourceChangedAtSchema,
  type CatalogueActionResult,
} from '@/lib/api/admin-catalogue-action';
import {
  applyErrorMessage,
  NO_PARTNER_UPDATE_MESSAGE,
  NO_SOURCE_CHANGE_MESSAGE,
  OFFER_CHANGED_MESSAGE,
  OFFER_NOT_FOUND_MESSAGE,
  PARTNER_UPDATE_ARCHIVED_MESSAGE,
  PARTNER_UPDATE_NOT_APPLICABLE_MESSAGE,
} from '@/lib/api/admin-catalogue-errors';
import {
  applyAdminOfferPartnerUpdate,
  createAdminOffer,
  dismissAdminOfferPartnerUpdate,
  dismissAdminOfferSourceChange,
  fetchAdminOffer,
  patchAdminOffer,
  runAdminOfferAction,
  type OfferActionRequest,
} from '@/lib/api/admin-offers';
import { toCreateOfferBody, toUpdateOfferBody } from '@/lib/api/admin-offers-body';
import { ADMIN_REQUEST_FAILED_MESSAGE } from '@/lib/api/admin-errors';

/**
 * Admin offer actions. Each one verifies the session and the admin first (`asAdmin`), validates the input with
 * the same Zod schema the form uses, calls the admin offers API through its wrapper and answers with curated
 * messages only. The API checks the admin's own permission (`offers.write` or `offers.publish`) and audits the
 * change. After a write the admin list refreshes and, when the public site can be affected, so does the
 * public catalogue (`revalidateCatalogue`).
 */

export type OfferSavedData = { id: string; slug: string; status: OfferStatus };
/**
 * What an edit or a status action returns: the saved offer, the review token of the version now stored and the
 * token of the version the write replaced (when they differ from the one the editor loaded, somebody else changed
 * the offer first). Either is absent when the API sent something that is not a plain token.
 */
export type OfferWrittenData = OfferSavedData & {
  reviewToken?: string;
  previousReviewToken?: string;
};
export type OfferActionData = OfferWrittenData;

const ADMIN_OFFERS_PATH = '/admin/offers';

function refreshAdminPages(offerId: string): void {
  revalidatePath(ADMIN_OFFERS_PATH);
  revalidatePath(`${ADMIN_OFFERS_PATH}/${offerId}`);
}

/** Creates a draft offer. A draft is not public, so only the admin pages refresh. */
export async function createOfferAction(
  input: unknown
): Promise<CatalogueActionResult<OfferSavedData>> {
  return asAdmin(async () => {
    const parsed = offerFormSchema.safeParse(input);
    if (!parsed.success) return invalidForm(parsed.error);

    const result = await createAdminOffer(toCreateOfferBody(parsed.data));
    if (!result.success)
      return failed(result, OFFER_NOT_FOUND_MESSAGE, 'Admin create offer failed');

    // The id goes straight into the browser's router.push: only a plain id is ever handed back.
    if (!resourceIdSchema.safeParse(result.data.id).success) {
      logger.error('Admin create offer: the API returned an unusable id');
      return { success: false, error: ADMIN_REQUEST_FAILED_MESSAGE };
    }
    revalidatePath(ADMIN_OFFERS_PATH);
    const { id, slug, status } = result.data;
    return { success: true, data: { id, slug, status: status as OfferStatus } };
  });
}

/**
 * Saves an edit. `reviewToken` is the token of the version the form shows: the API needs it when the commercial
 * basis changes and answers 409 `offer_changed` when it is missing or stale (nothing is written). That refusal
 * gets the curated "review it again" message and refreshes the page so it shows the current version.
 */
export async function updateOfferAction(
  offerId: string,
  input: unknown,
  reviewToken?: string
): Promise<CatalogueActionResult<OfferWrittenData>> {
  return asAdmin(async () => {
    if (!resourceIdSchema.safeParse(offerId).success) return notFound(OFFER_NOT_FOUND_MESSAGE);
    // An offer's basis may be one nobody has chosen yet (a retailer's submission): it is then left out, not refused.
    const parsed = offerEditSchema.safeParse(input);
    if (!parsed.success) return invalidForm(parsed.error);

    const token = reviewTokenSchema.safeParse(reviewToken);
    const result = await patchAdminOffer(
      offerId,
      toUpdateOfferBody(parsed.data, token.success ? token.data : undefined, {
        basisOptional: true,
      })
    );
    if (!result.success) {
      const refusal = failed(result, OFFER_NOT_FOUND_MESSAGE, 'Admin update offer failed');
      if (result.code !== 'offer_changed') return refusal;
      refreshAdminPages(offerId);
      return { ...refusal, reason: 'offer_changed' };
    }

    refreshAdminPages(offerId);
    const { id, slug, status } = result.data;
    if (status === 'PUBLISHED') refreshPublicCatalogue(slug);
    return {
      success: true,
      data: {
        id,
        slug,
        status: status as OfferStatus,
        reviewToken: plainToken(result.data.review_token),
        previousReviewToken: plainToken(result.data.previous_review_token),
      },
    };
  });
}

/** A review token from the API, only if it is made of the plain characters tokens have. */
function plainToken(value: unknown): string | undefined {
  const parsed = reviewTokenSchema.safeParse(value);
  return parsed.success ? parsed.data : undefined;
}

/**
 * What the API call needs for `action`, or null when a reviewed step has no usable review token. Only publish
 * carries a commercial basis, and only a known one: anything else is left out, so the API answers that the basis
 * must be confirmed.
 */
function toActionRequest(
  action: OfferAction,
  reviewToken: unknown,
  commercialBasis: unknown
): OfferActionRequest | null {
  if (!isReviewedOfferAction(action)) return { action };
  const token = reviewTokenSchema.safeParse(reviewToken);
  if (!token.success) return null;
  if (action === 'publish') {
    return isCommercialBasis(commercialBasis)
      ? { action, reviewToken: token.data, commercialBasis }
      : { action, reviewToken: token.data };
  }
  return { action, reviewToken: token.data };
}

/**
 * Dismisses the "source changed" marker of an imported offer: the admin has seen that the source moved on and
 * keeps the offer as it is. It needs the `reviewToken` of the offer the admin loaded and the `sourceChangedAt` the
 * page showed (the token does not cover the marker): 409 `offer_changed` when either moved since, handled like
 * verify and publish. A dismissal changes nothing a visitor sees, so only the admin
 * pages refresh. When the API sees no marker any more (somebody dismissed it first) the page is refreshed so it
 * catches up and the admin is told so.
 */
export async function dismissSourceChangeAction(
  offerId: string,
  reviewToken?: string,
  sourceChangedAt?: string
): Promise<CatalogueActionResult<OfferActionData>> {
  return asAdmin(async () => {
    if (!resourceIdSchema.safeParse(offerId).success) return notFound(OFFER_NOT_FOUND_MESSAGE);
    const token = reviewTokenSchema.safeParse(reviewToken);
    const marker = sourceChangedAtSchema.safeParse(sourceChangedAt);
    if (!token.success || !marker.success) {
      // Only the action name: never the token or the marker value.
      logger.warn('Admin offer action: review token or source marker missing or malformed', {
        action: 'dismiss_source_change',
      });
      return { success: false, error: OFFER_CHANGED_MESSAGE };
    }

    const result = await dismissAdminOfferSourceChange(offerId, token.data, marker.data);
    if (!result.success) {
      const refusal = failed(result, OFFER_NOT_FOUND_MESSAGE, 'Admin dismiss source change failed');
      if (result.code === 'offer_changed') {
        refreshAdminPages(offerId);
        return { ...refusal, reason: 'offer_changed' };
      }
      if (result.code === 'illegal_transition') {
        refreshAdminPages(offerId);
        return { success: false, error: NO_SOURCE_CHANGE_MESSAGE };
      }
      return refusal;
    }

    refreshAdminPages(offerId);
    const { id, slug, status } = result.data;
    return {
      success: true,
      data: {
        id,
        slug,
        status: status as OfferStatus,
        reviewToken: plainToken(result.data.review_token),
        previousReviewToken: plainToken(result.data.previous_review_token),
      },
    };
  });
}

/**
 * Publish, unpublish, expire, archive or verify. Every one can change what visitors see, so all refresh it.
 *
 * Publish and verify must carry the `reviewToken` of the offer the admin loaded. Without a usable one nothing is
 * sent. When the API says the offer changed since (409 `offer_changed`) the admin pages are refreshed so the next
 * load shows the new content and a fresh token, and the admin gets the curated "review it again" message.
 *
 * Publish may also carry `commercialBasis`, the basis the admin confirmed for an offer whose only active links
 * are `direct` (the API refuses to publish that without it).
 */
export async function offerLifecycleAction(
  offerId: string,
  action: OfferAction,
  reviewToken?: string,
  commercialBasis?: string
): Promise<CatalogueActionResult<OfferActionData>> {
  return asAdmin(async () => {
    if (!resourceIdSchema.safeParse(offerId).success) return notFound(OFFER_NOT_FOUND_MESSAGE);
    if (!isOfferAction(action)) return { success: false, error: ADMIN_REQUEST_FAILED_MESSAGE };
    const request = toActionRequest(action, reviewToken, commercialBasis);
    if (request === null) {
      // Only the action name: never the token value.
      logger.warn('Admin offer action: review token missing or malformed', { action });
      return { success: false, error: OFFER_CHANGED_MESSAGE };
    }

    const result = await runAdminOfferAction(offerId, request);
    if (!result.success) {
      const refusal = failed(result, OFFER_NOT_FOUND_MESSAGE, `Admin offer ${action} failed`);
      if (result.code !== 'offer_changed') return refusal;
      refreshAdminPages(offerId);
      return { ...refusal, reason: 'offer_changed' };
    }

    refreshAdminPages(offerId);
    refreshPublicCatalogue(result.data.slug);
    const { id, slug, status } = result.data;
    return {
      success: true,
      data: {
        id,
        slug,
        status: status as OfferStatus,
        reviewToken: plainToken(result.data.review_token),
        previousReviewToken: plainToken(result.data.previous_review_token),
      },
    };
  });
}

type PartnerUpdateCall = typeof applyAdminOfferPartnerUpdate;
type PartnerUpdateName = 'apply_partner_update' | 'dismiss_partner_update';

/**
 * What a 409 `illegal_transition` means for a retailer update. For a dismissal it is always "nothing waiting". For an
 * apply the API gives the same answer for an archived offer (which only allows dismissing), so the offer is read
 * to tell the two apart; when it cannot be read the message claims neither.
 */
async function illegalTransitionMessage(name: PartnerUpdateName, offerId: string): Promise<string> {
  if (name === 'dismiss_partner_update') return NO_PARTNER_UPDATE_MESSAGE;
  const offer = await fetchAdminOffer(offerId);
  if (!offer.success) return PARTNER_UPDATE_NOT_APPLICABLE_MESSAGE;
  return offer.data.status === 'ARCHIVED'
    ? PARTNER_UPDATE_ARCHIVED_MESSAGE
    : NO_PARTNER_UPDATE_MESSAGE;
}

/**
 * The shared body of applying or dismissing a retailer's update: the id, the review token and the proposal hash
 * the page showed are validated before the API is called (a malformed one is the curated "changed since you opened
 * it", with only the action name logged), every failure is a curated message and a success refreshes the admin
 * pages. `publicToo` also refreshes the public catalogue: applying edits what a live offer says.
 */
async function partnerUpdateAction(
  name: PartnerUpdateName,
  call: PartnerUpdateCall,
  offerId: string,
  reviewToken: string | undefined,
  pendingHash: string | undefined,
  publicToo: boolean
): Promise<CatalogueActionResult<OfferActionData>> {
  return asAdmin(async () => {
    if (!resourceIdSchema.safeParse(offerId).success) return notFound(OFFER_NOT_FOUND_MESSAGE);
    const token = reviewTokenSchema.safeParse(reviewToken);
    const hash = pendingHashSchema.safeParse(pendingHash);
    if (!token.success || !hash.success) {
      logger.warn('Admin offer action: review token or proposal hash missing or malformed', {
        action: name,
      });
      return { success: false, error: OFFER_CHANGED_MESSAGE };
    }

    const result = await call(offerId, token.data, hash.data);
    if (!result.success) {
      const refusal = failed(result, OFFER_NOT_FOUND_MESSAGE, `Admin ${name} failed`);
      if (result.code === 'offer_changed') {
        refreshAdminPages(offerId);
        return { ...refusal, reason: 'offer_changed' };
      }
      if (result.code === 'illegal_transition') {
        refreshAdminPages(offerId);
        return { success: false, error: await illegalTransitionMessage(name, offerId) };
      }
      const specific = name === 'apply_partner_update' ? applyErrorMessage(result) : undefined;
      return specific === undefined ? refusal : { success: false, error: specific };
    }

    refreshAdminPages(offerId);
    const { id, slug, status } = result.data;
    if (publicToo && status === 'PUBLISHED') refreshPublicCatalogue(slug);
    return {
      success: true,
      data: {
        id,
        slug,
        status: status as OfferStatus,
        reviewToken: plainToken(result.data.review_token),
        previousReviewToken: plainToken(result.data.previous_review_token),
      },
    };
  });
}

/**
 * Applies the update a retailer sent for an offer an editor has touched: the API writes it through the normal edit
 * path, after the review token and the proposal hash the page showed check out. It needs `offers.write` (and
 * `offers.publish` for a live offer); the API checks the admin's own permission.
 */
export async function applyPartnerUpdateAction(
  offerId: string,
  reviewToken?: string,
  pendingHash?: string
): Promise<CatalogueActionResult<OfferActionData>> {
  return partnerUpdateAction(
    'apply_partner_update',
    applyAdminOfferPartnerUpdate,
    offerId,
    reviewToken,
    pendingHash,
    true
  );
}

/** Dismisses the update a retailer sent: it is forgotten and the offer leaves the review queue; nothing is edited. */
export async function dismissPartnerUpdateAction(
  offerId: string,
  reviewToken?: string,
  pendingHash?: string
): Promise<CatalogueActionResult<OfferActionData>> {
  return partnerUpdateAction(
    'dismiss_partner_update',
    dismissAdminOfferPartnerUpdate,
    offerId,
    reviewToken,
    pendingHash,
    false
  );
}
