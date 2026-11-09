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
  type CatalogueActionResult,
} from '@/lib/api/admin-catalogue-action';
import { OFFER_CHANGED_MESSAGE, OFFER_NOT_FOUND_MESSAGE } from '@/lib/api/admin-catalogue-errors';
import {
  createAdminOffer,
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
export type OfferActionData = OfferSavedData;

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
): Promise<CatalogueActionResult<OfferSavedData>> {
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
    return { success: true, data: { id, slug, status: status as OfferStatus } };
  });
}

/** What the API call needs for `action`, or null when a reviewed step has no usable review token. */
function toActionRequest(action: OfferAction, reviewToken: unknown): OfferActionRequest | null {
  if (!isReviewedOfferAction(action)) return { action };
  const token = reviewTokenSchema.safeParse(reviewToken);
  return token.success ? { action, reviewToken: token.data } : null;
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
    if (request === null) return { success: false, error: OFFER_CHANGED_MESSAGE };

    const result = await runAdminOfferAction(offerId, request);
    if (!result.success) {
      if (result.code === 'offer_changed') refreshAdminPages(offerId);
      return failed(result, OFFER_NOT_FOUND_MESSAGE, `Admin offer ${action} failed`);
    }

    refreshAdminPages(offerId);
    refreshPublicCatalogue(result.data.slug);
    const { id, slug, status } = result.data;
    return { success: true, data: { id, slug, status: status as OfferStatus } };
  });
}
