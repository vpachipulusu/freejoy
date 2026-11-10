'use client';

import { dismissSourceChangeAction } from '@/app/actions/admin/offer-actions';
import { useOfferWrite } from './use-offer-write';

/**
 * Dismisses the "source changed" marker of an imported offer through its server action, wired like Mark verified
 * (`useOfferWrite`). It sends the review token the page loaded and the `source_changed_at` the panel showed: the
 * token does not cover the marker, so the API refuses (409 `offer_changed`) when the importer noted a newer one.
 */
export function useDismissSourceChange() {
  const { run: write, isPending } = useOfferWrite();

  function run(
    offerId: string,
    offerTitle: string,
    reviewToken: string,
    sourceChangedAt: string
  ): void {
    write({
      call: () => dismissSourceChangeAction(offerId, reviewToken, sourceChangedAt),
      successTitle: 'Source change dismissed',
      failureTitle: 'Could not dismiss the source change',
      description: offerTitle,
    });
  }

  return { run, isPending };
}
