'use server';

import {
  buildClicksCsv,
  clicksCsvFilename,
  CSV_TOO_LARGE_MESSAGE,
  csvByteLength,
  MAX_CSV_BYTES,
} from '@/lib/admin-clicks/csv';
import { exportClicksInputSchema } from '@/lib/admin-clicks/range';
import {
  asAdmin,
  failed,
  invalidForm,
  type CatalogueActionResult,
} from '@/lib/api/admin-catalogue-action';
import { CLICKS_MERCHANT_NOT_FOUND_MESSAGE } from '@/lib/api/admin-catalogue-errors';
import { fetchMerchantClicks } from '@/lib/api/admin-analytics';

/**
 * Builds the click report of one merchant as a CSV file. Same contract as the other admin actions: the admin gate
 * first (a refusal calls nothing), input validated with Zod, the API wrapper, curated messages only. The API
 * checks `offers.read` itself. `truncated` tells the page the report hit the API's offer limit, so the file does
 * not list every offer of the merchant. A file over `MAX_CSV_BYTES` is refused with a curated message (the page
 * asks for a shorter range) instead of travelling as one very large string. The file name is built from the
 * validated dates and the merchant slug the API returned.
 */

export type ClicksCsvData = { filename: string; csv: string; truncated: boolean };

export async function exportMerchantClicksAction(
  input: unknown
): Promise<CatalogueActionResult<ClicksCsvData>> {
  return asAdmin(async () => {
    const parsed = exportClicksInputSchema.safeParse(input);
    if (!parsed.success) return invalidForm(parsed.error);

    const result = await fetchMerchantClicks(parsed.data);
    if (!result.success) {
      return failed(
        result,
        CLICKS_MERCHANT_NOT_FOUND_MESSAGE,
        'Admin merchant click export failed'
      );
    }
    const report = result.data;
    const csv = buildClicksCsv(report);
    if (csvByteLength(csv) > MAX_CSV_BYTES) return { success: false, error: CSV_TOO_LARGE_MESSAGE };
    return {
      success: true,
      data: {
        filename: clicksCsvFilename(report.merchant.slug, parsed.data.from, parsed.data.to),
        csv,
        truncated: report.offers_truncated,
      },
    };
  });
}
