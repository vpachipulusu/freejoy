import 'server-only';

import { z } from 'zod';
import { logger } from '@/lib/logger';
import { revalidateCatalogue } from '@/lib/catalogue/revalidate';
import { logCatalogueFailure } from './admin-catalogue-log';
import { requireAdminSession } from './admin-guard';
import { catalogueErrorMessage, INVALID_FORM_MESSAGE } from './admin-catalogue-errors';
import type { ApiResult } from './server';

/**
 * Shared plumbing for the admin catalogue server actions: the admin gate, input validation that answers with
 * per-field messages, curated failures and the public-page refresh. Nothing here returns backend text.
 */

export type CatalogueActionResult<T = undefined> =
  | { success: true; data: T }
  | {
      success: false;
      error: string;
      fieldErrors?: Record<string, string>;
      /** Set when the refusal means the offer changed since it was loaded: the page shows a lasting notice. */
      reason?: 'offer_changed';
    };

type FailedResult = Extract<ApiResult<unknown>, { success: false }>;

/** Resource ids go into URL paths: only the characters real ids contain are accepted. */
export const resourceIdSchema = z.string().regex(/^[A-Za-z0-9_-]{1,128}$/);

/** The opaque review token of an offer goes into a request body: only its plain characters are accepted. */
export const reviewTokenSchema = z.string().regex(/^[A-Za-z0-9_-]{1,128}$/);

const SLUG_FOR_PATH = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

/** Runs `action` only for a confirmed admin; everyone else gets the curated refusal and nothing is called. */
export async function asAdmin<T>(
  action: () => Promise<CatalogueActionResult<T>>
): Promise<CatalogueActionResult<T>> {
  const gate = await requireAdminSession();
  if (!gate.ok) return { success: false, error: gate.error };
  return action();
}

export function failed(
  result: FailedResult,
  notFoundMessage: string,
  logMessage: string
): { success: false; error: string; fieldErrors?: Record<string, string> } {
  logCatalogueFailure(logMessage, result);
  const error = catalogueErrorMessage(result, notFoundMessage);
  // A slug refusal belongs to the slug field (offers, categories and merchants all have one).
  if (result.code === 'slug_taken' || result.code === 'invalid_slug') {
    return { success: false, error, fieldErrors: { slug: error } };
  }
  return { success: false, error };
}

/** A failed parse as per-field messages (the first message of each field, keyed by its dotted path). */
export function invalidForm(error: z.ZodError): {
  success: false;
  error: string;
  fieldErrors: Record<string, string>;
} {
  const fieldErrors: Record<string, string> = {};
  for (const issue of error.issues) {
    const key = issue.path.join('.');
    if (key !== '' && !(key in fieldErrors)) fieldErrors[key] = issue.message;
  }
  return { success: false, error: INVALID_FORM_MESSAGE, fieldErrors };
}

export function notFound(message: string): { success: false; error: string } {
  return { success: false, error: message };
}

/**
 * Refreshes the public catalogue after a write. The write has already happened, so a failure here is logged
 * and never turned into a failed action. `slug` adds that offer's own page.
 */
export function refreshPublicCatalogue(slug?: string): void {
  try {
    const paths = ['/freebies'];
    if (slug !== undefined && SLUG_FOR_PATH.test(slug)) paths.push(`/freebies/${slug}`);
    revalidateCatalogue(paths);
  } catch {
    logger.error('Admin catalogue: public page refresh failed');
  }
}
