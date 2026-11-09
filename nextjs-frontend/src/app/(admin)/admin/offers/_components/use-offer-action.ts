'use client';

import { useRef, useTransition } from 'react';
import { useRouter } from 'next/navigation';
import { offerLifecycleAction } from '@/app/actions/admin/offer-actions';
import type { OfferAction } from '@/lib/admin-catalogue/offer-status';
import { settleAction } from '@/lib/admin-catalogue/settle-action';
import { useToast } from '@/hooks/use-toast';
import { useOfferReview } from './offer-review-context';

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
 * Runs an offer lifecycle action through its server action and reports the outcome in a toast (an announced,
 * dismissible message). The action refreshes the admin pages and the public catalogue; `router.refresh()` makes
 * the current page pick the change up straight away (after a refused "changed since you opened it" it loads
 * the new content too). Publish and verify send the review token of the offer as it was loaded. Only one action runs at a time: a second request while one
 * is in flight is ignored (the controls are aria-disabled, which keeps focus, so the guard is here as well).
 */
export function useOfferAction() {
  const router = useRouter();
  const { toast } = useToast();
  const review = useOfferReview();
  const [isPending, startTransition] = useTransition();
  const busy = useRef(false);

  function run(
    offerId: string,
    action: OfferAction,
    offerTitle: string,
    reviewToken?: string
  ): void {
    if (busy.current) return;
    busy.current = true;
    review.beginWrite();
    startTransition(async () => {
      try {
        const result = await settleAction(() => offerLifecycleAction(offerId, action, reviewToken));
        if (result.success) {
          review.recordWrite(result.data.reviewToken, result.data.previousReviewToken);
          toast({ title: SUCCESS_TITLES[action], description: offerTitle });
        } else {
          review.recordFailure(result.reason);
          toast({
            variant: 'destructive',
            title: FAILURE_TITLES[action],
            description: result.error,
          });
        }
        router.refresh();
      } finally {
        busy.current = false;
      }
    });
  }

  return { run, isPending };
}
