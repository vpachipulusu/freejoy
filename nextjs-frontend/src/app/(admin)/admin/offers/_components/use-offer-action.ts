'use client';

import { offerLifecycleAction } from '@/app/actions/admin/offer-actions';
import type { OfferAction } from '@/lib/admin-catalogue/offer-status';
import { useOfferWrite } from './use-offer-write';

const SUCCESS_TITLES: Record<OfferAction, string> = {
  publish: 'Offer published',
  unpublish: 'Offer unpublished and back to draft',
  expire: 'Offer marked as expired',
  archive: 'Offer archived',
  verify: 'Offer marked as verified',
};

const FAILURE_TITLES: Record<OfferAction, string> = {
  publish: 'Could not publish',
  unpublish: 'Could not unpublish',
  expire: 'Could not expire',
  archive: 'Could not archive',
  verify: 'Could not mark as verified',
};

/**
 * Runs an offer lifecycle action through its server action (see `useOfferWrite` for how the outcome is
 * reported). The action refreshes the admin pages and the public catalogue. Publish and verify send the review
 * token of the offer as it was loaded.
 */
export function useOfferAction() {
  const { run: write, isPending } = useOfferWrite();

  function run(
    offerId: string,
    action: OfferAction,
    offerTitle: string,
    reviewToken?: string
  ): void {
    write({
      call: () => offerLifecycleAction(offerId, action, reviewToken),
      successTitle: SUCCESS_TITLES[action],
      failureTitle: FAILURE_TITLES[action],
      description: offerTitle,
    });
  }

  return { run, isPending };
}
