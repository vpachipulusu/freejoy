'use server';

import { revalidatePath } from 'next/cache';
import {
  partnerKeyFormSchema,
  type PartnerKeyView,
  type IssuedPartnerKeyView,
} from '@/lib/admin-catalogue/partner-key-schema';
import {
  asAdmin,
  failed,
  invalidForm,
  notFound,
  resourceIdSchema,
  type CatalogueActionResult,
} from '@/lib/api/admin-catalogue-action';
import { PARTNER_KEY_NOT_FOUND_MESSAGE } from '@/lib/api/admin-catalogue-errors';
import {
  createAdminPartnerKey,
  revokeAdminPartnerKey,
  toPartnerKeyView,
} from '@/lib/api/admin-partner-keys';

/**
 * Admin partner key actions: same contract as the merchant actions (admin gate first, Zod validation, the
 * wrapper, curated messages). The API needs `partner_keys.write` (a dedicated permission). The key value is returned to the browser once, by
 * `createPartnerKeyAction`, and appears nowhere else: not in a log, not in a failure message, not in a path.
 */

const MERCHANT_PATH = '/admin/merchants';

export async function createPartnerKeyAction(
  merchantId: string,
  input: unknown
): Promise<CatalogueActionResult<IssuedPartnerKeyView>> {
  return asAdmin(async () => {
    if (!resourceIdSchema.safeParse(merchantId).success)
      return notFound(PARTNER_KEY_NOT_FOUND_MESSAGE);
    const parsed = partnerKeyFormSchema.safeParse(input);
    if (!parsed.success) return invalidForm(parsed.error);

    const result = await createAdminPartnerKey(merchantId, parsed.data.label);
    if (!result.success) {
      const outcome = failed(
        result,
        PARTNER_KEY_NOT_FOUND_MESSAGE,
        'Admin create partner key failed'
      );
      // A label the API refuses as key-looking belongs to the label field, not to a line above the form.
      return result.code === 'invalid_label'
        ? { ...outcome, fieldErrors: { label: outcome.error } }
        : outcome;
    }
    revalidatePath(`${MERCHANT_PATH}/${merchantId}`);
    return {
      success: true,
      data: { key: toPartnerKeyView(result.data.key), apiKey: result.data.api_key },
    };
  });
}

export async function revokePartnerKeyAction(
  merchantId: string,
  keyId: string
): Promise<CatalogueActionResult<PartnerKeyView>> {
  return asAdmin(async () => {
    if (
      !resourceIdSchema.safeParse(merchantId).success ||
      !resourceIdSchema.safeParse(keyId).success
    )
      return notFound(PARTNER_KEY_NOT_FOUND_MESSAGE);

    const result = await revokeAdminPartnerKey(merchantId, keyId);
    if (!result.success) {
      return failed(result, PARTNER_KEY_NOT_FOUND_MESSAGE, 'Admin revoke partner key failed');
    }
    revalidatePath(`${MERCHANT_PATH}/${merchantId}`);
    return { success: true, data: toPartnerKeyView(result.data) };
  });
}
