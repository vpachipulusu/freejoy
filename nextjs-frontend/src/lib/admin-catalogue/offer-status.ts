import { Pencil, type LucideIcon } from 'lucide-react';
import type { StatusPillVariant } from '@/components/ui/status-pill';

/** Offer lifecycle states as the admin catalogue API reports them. */
export const OFFER_STATUSES = ['DRAFT', 'PUBLISHED', 'EXPIRED', 'ARCHIVED'] as const;
export type OfferStatus = (typeof OFFER_STATUSES)[number];

/** A status the API sent that this UI does not know (a newer API): shown neutral, never acted on. */
export type OfferRowStatus = OfferStatus | 'UNKNOWN';

export const OFFER_STATUS_LABELS: Record<OfferRowStatus, string> = {
  DRAFT: 'Draft',
  PUBLISHED: 'Published',
  EXPIRED: 'Expired',
  ARCHIVED: 'Archived',
  UNKNOWN: 'Unknown',
};

/** Pill tints: good = Published, neutral = Draft, warn = Expired, info = Archived. */
export const OFFER_STATUS_VARIANTS: Record<OfferRowStatus, StatusPillVariant> = {
  DRAFT: 'neutral',
  PUBLISHED: 'good',
  EXPIRED: 'warn',
  ARCHIVED: 'info',
  UNKNOWN: 'neutral',
};

/** Only a draft overrides the pill's default icon (a pencil: not live yet, still being written). */
export const OFFER_STATUS_ICONS: Partial<Record<OfferRowStatus, LucideIcon>> = {
  DRAFT: Pencil,
};

export const OFFER_ACTIONS = ['publish', 'unpublish', 'expire', 'archive', 'verify'] as const;
export type OfferAction = (typeof OFFER_ACTIONS)[number];

/**
 * The steps that say "a person reviewed this exact offer": the API needs the review token of the offer as it was
 * loaded and refuses (409 `offer_changed`) when it changed since. Taking an offer off the site needs none.
 */
export const REVIEWED_OFFER_ACTIONS = ['publish', 'verify'] as const;
export type ReviewedOfferAction = (typeof REVIEWED_OFFER_ACTIONS)[number];

export function isReviewedOfferAction(action: OfferAction): action is ReviewedOfferAction {
  return (REVIEWED_OFFER_ACTIONS as readonly string[]).includes(action);
}

export const OFFER_ACTION_LABELS: Record<OfferAction, string> = {
  publish: 'Publish',
  unpublish: 'Unpublish',
  expire: 'Expire',
  archive: 'Archive',
  verify: 'Mark verified',
};

/**
 * Which actions the menu offers for each status. This mirrors the backend status table
 * (`TRANSITIONS` in `app/services/offer_lifecycle.py`, documented in backend-admin-catalogue.md) and is a UI
 * hint only: the API refuses anything else with 409 `illegal_transition`, so a stale menu is harmless.
 */
const ACTIONS_BY_STATUS: Record<OfferRowStatus, readonly OfferAction[]> = {
  DRAFT: ['verify', 'publish', 'archive'],
  PUBLISHED: ['verify', 'unpublish', 'expire', 'archive'],
  EXPIRED: ['verify', 'publish', 'archive'],
  ARCHIVED: [],
  UNKNOWN: [],
};

export function availableOfferActions(status: OfferRowStatus): readonly OfferAction[] {
  return ACTIONS_BY_STATUS[status];
}

export function isOfferStatus(value: unknown): value is OfferStatus {
  return typeof value === 'string' && (OFFER_STATUSES as readonly string[]).includes(value);
}

export function isOfferAction(value: unknown): value is OfferAction {
  return typeof value === 'string' && (OFFER_ACTIONS as readonly string[]).includes(value);
}

/** The public page exists only for a published offer; every other status is a 404 or an "ended" page. */
export function publicOfferPath(status: OfferRowStatus, slug: string): string | null {
  return status === 'PUBLISHED' ? `/freebies/${slug}` : null;
}
