'use server';

import { revalidatePath } from 'next/cache';
import { logger } from '@/lib/logger';
import { offerFormSchema } from '@/lib/admin-catalogue/offer-schema';
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
  refreshPublicCatalogue,
  resourceIdSchema,
  reviewTokenSchema,
  sourceChangedAtSchema,
  type CatalogueActionResult,
} from '@/lib/api/admin-catalogue-action';
import {
  NO_SOURCE_CHANGE_MESSAGE,
  OFFER_CHANGED_MESSAGE,
  OFFER_NOT_FOUND_MESSAGE,
} from '@/lib/api/admin-catalogue-errors';
import {
  createAdminOffer,
  dismissAdminOfferSourceChange,
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

export async function updateOfferAction(
  offerId: string,
  input: unknown
): Promise<CatalogueActionResult<OfferWrittenData>> {
  return asAdmin(async () => {
    if (!resourceIdSchema.safeParse(offerId).success) return notFound(OFFER_NOT_FOUND_MESSAGE);
    const parsed = offerFormSchema.safeParse(input);
    if (!parsed.success) return invalidForm(parsed.error);

    const result = await patchAdminOffer(offerId, toUpdateOfferBody(parsed.data));
    if (!result.success)
      return failed(result, OFFER_NOT_FOUND_MESSAGE, 'Admin update offer failed');

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

/** What the API call needs for `action`, or null when a reviewed step has no usable review token. */
function toActionRequest(action: OfferAction, reviewToken: unknown): OfferActionRequest | null {
  if (!isReviewedOfferAction(action)) return { action };
  const token = reviewTokenSchema.safeParse(reviewToken);
  return token.success ? { action, reviewToken: token.data } : null;
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
 */
export async function offerLifecycleAction(
  offerId: string,
  action: OfferAction,
  reviewToken?: string
): Promise<CatalogueActionResult<OfferActionData>> {
  return asAdmin(async () => {
    if (!resourceIdSchema.safeParse(offerId).success) return notFound(OFFER_NOT_FOUND_MESSAGE);
    if (!isOfferAction(action)) return { success: false, error: ADMIN_REQUEST_FAILED_MESSAGE };
    const request = toActionRequest(action, reviewToken);
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
