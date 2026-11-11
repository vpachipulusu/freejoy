'use client';

import {
  applyPartnerUpdateAction,
  dismissPartnerUpdateAction,
} from '@/app/actions/admin/offer-actions';
import { useOfferWrite } from './use-offer-write';

/**
 * Applies or dismisses a retailer's update through its server action, wired like Mark verified (`useOfferWrite`):
 * the review state is told the write began and how it ended, a toast announces the outcome and the page reloads.
 * Both send the review token the page loaded and the hash of the proposal it showed, so the API refuses (409
 * `offer_changed`) when either moved since.
 */
export function usePartnerUpdate() {
  const { run: write, isPending } = useOfferWrite();

  function apply(offerId: string, offerTitle: string, reviewToken: string, pendingHash: string) {
    write({
      call: () => applyPartnerUpdateAction(offerId, reviewToken, pendingHash),
      successTitle: 'Retailer update applied',
      failureTitle: 'Could not apply the retailer update',
      description: offerTitle,
    });
  }

  function dismiss(offerId: string, offerTitle: string, reviewToken: string, pendingHash: string) {
    write({
      call: () => dismissPartnerUpdateAction(offerId, reviewToken, pendingHash),
      successTitle: 'Retailer update dismissed',
      failureTitle: 'Could not dismiss the retailer update',
      description: offerTitle,
    });
  }

  return { apply, dismiss, isPending };
}
