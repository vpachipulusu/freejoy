'use client';

import { useState } from 'react';
import {
  applyPartnerUpdateAction,
  dismissPartnerUpdateAction,
} from '@/app/actions/admin/offer-actions';
import { useOfferWrite, type OfferWriteRequest } from './use-offer-write';

/**
 * Applies or dismisses a retailer's update through its server action, wired like Mark verified (`useOfferWrite`):
 * the review state is told the write began and how it ended, a toast announces the outcome and the page reloads.
 * Both send the review token the page loaded and the hash of the proposal it showed, so the API refuses (409
 * `offer_changed`) when either moved since.
 *
 * A failure is also kept as `failure`, the curated message, until the next attempt starts: a toast is gone after a
 * few seconds and the section needs a message that stays.
 */
export function usePartnerUpdate() {
  const { run: write, isPending } = useOfferWrite();
  const [failure, setFailure] = useState<string | null>(null);

  function start(request: Omit<OfferWriteRequest, 'onFailure'>): void {
    setFailure(null);
    write({ ...request, onFailure: setFailure });
  }

  function apply(offerId: string, offerTitle: string, reviewToken: string, pendingHash: string) {
    start({
      call: () => applyPartnerUpdateAction(offerId, reviewToken, pendingHash),
      successTitle: 'Retailer update applied',
      failureTitle: 'Could not apply the retailer update',
      description: offerTitle,
    });
  }

  function dismiss(offerId: string, offerTitle: string, reviewToken: string, pendingHash: string) {
    start({
      call: () => dismissPartnerUpdateAction(offerId, reviewToken, pendingHash),
      successTitle: 'Retailer update dismissed',
      failureTitle: 'Could not dismiss the retailer update',
      description: offerTitle,
    });
  }

  return { apply, dismiss, isPending, failure };
}
