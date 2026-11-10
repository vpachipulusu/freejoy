import 'server-only';

import {
  ADMIN_REQUEST_FAILED_MESSAGE,
  adminErrorMessage as baseAdminErrorMessage,
} from './admin-errors';
import type { ApiResult } from './server';

/**
 * Curated messages for failed admin catalogue calls. The backend sends a stable `code` with its 404, 409 and 422
 * answers; the code (never the backend text) picks the wording, so ids, URLs and provider text cannot reach the
 * admin's browser. A refused publish lists its unmet rules, each taken from this fixed table.
 */

type FailedResult = Extract<ApiResult<unknown>, { success: false }>;

export const OFFER_NOT_FOUND_MESSAGE = 'That offer no longer exists. Go back to the list.';
export const CATEGORY_NOT_FOUND_MESSAGE = 'That category no longer exists. Go back to the list.';
export const MERCHANT_NOT_FOUND_MESSAGE = 'That merchant no longer exists. Go back to the list.';
export const OFFER_CHANGED_MESSAGE = 'This offer changed since you opened it. Review it again.';
export const NO_SOURCE_CHANGE_MESSAGE =
  'There is no source change to dismiss on this offer. This page has been refreshed.';
export const RATE_LIMITED_MESSAGE = 'Too many requests. Wait a minute and try again.';
export const INVALID_INPUT_MESSAGE = 'Some values were rejected. Check the form and try again.';
export const INVALID_FORM_MESSAGE = 'Check the highlighted fields and try again.';

const CODE_MESSAGES: Record<string, string> = {
  slug_taken: 'That slug is already in use. Choose another.',
  offer_changed: OFFER_CHANGED_MESSAGE,
  review_token_required: OFFER_CHANGED_MESSAGE,
  illegal_transition:
    'That change is not allowed for an offer in its current status. Refresh the page and try again.',
  invalid_url: 'A web address was rejected. Use a full https:// address.',
  invalid_slug: 'The slug is not valid. Use lower-case letters, numbers and single hyphens.',
  invalid_postage: 'The postage amount does not match the postage type.',
  invalid_window: 'The end date must be after the start date.',
  unknown_category: 'That category does not exist. Choose another.',
  unknown_merchant: 'That merchant does not exist. Choose another.',
};

/** The backend's fixed publish rules (`PUBLISH_BLOCKERS`), mapped to our own wording. */
const PUBLISH_REASONS: Record<string, string> = {
  'its category is disabled': 'its category is disabled',
  'its merchant is inactive': 'its merchant is inactive',
  'it has no active affiliate link or source URL': 'it has no active affiliate link or source URL',
  'its end date has passed': 'its end date has passed',
  'it has not been verified': 'it has not been verified',
};

const PUBLISH_BLOCKED_PREFIX = 'The offer cannot be published yet: ';
const PUBLISH_BLOCKED_MESSAGE = 'This offer cannot be published yet.';

/** The unmet publish rules named in a `publish_blocked` answer, from the fixed table only. */
export function publishBlockedReasons(backendText: string): string[] {
  if (!backendText.startsWith(PUBLISH_BLOCKED_PREFIX)) return [];
  return backendText
    .slice(PUBLISH_BLOCKED_PREFIX.length)
    .split('; ')
    .map((reason) => PUBLISH_REASONS[reason])
    .filter((reason): reason is string => reason !== undefined);
}

function publishBlockedMessage(backendText: string): string {
  const reasons = publishBlockedReasons(backendText);
  if (reasons.length === 0) return PUBLISH_BLOCKED_MESSAGE;
  return `This offer cannot be published yet: ${reasons.join('; ')}.`;
}

/** `notFoundMessage` names what was not found (offer, category or merchant). */
export function catalogueErrorMessage(result: FailedResult, notFoundMessage: string): string {
  if (result.code === 'publish_blocked') return publishBlockedMessage(result.error);
  if (result.code !== undefined && CODE_MESSAGES[result.code] !== undefined) {
    return CODE_MESSAGES[result.code];
  }
  if (result.status === 429) return RATE_LIMITED_MESSAGE;
  if (result.status === 422) return INVALID_INPUT_MESSAGE;
  if (result.status === 409) return ADMIN_REQUEST_FAILED_MESSAGE;
  return baseAdminErrorMessage(result, notFoundMessage);
}
