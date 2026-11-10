'use client';

import { useRef, useTransition } from 'react';
import { useRouter } from 'next/navigation';
import { dismissSourceChangeAction } from '@/app/actions/admin/offer-actions';
import { settleAction } from '@/lib/admin-catalogue/settle-action';
import { useToast } from '@/hooks/use-toast';
import { useOfferReview } from './offer-review-context';

/**
 * Dismisses the "source changed" marker of an imported offer through its server action, wired like Mark verified
 * (`useOfferAction`): the review state is told the write began and how it ended (a stale token raises the lasting
 * "changed since you opened it" notice), a toast announces the outcome and `router.refresh()` loads the result.
 * Only one runs at a time.
 */
export function useDismissSourceChange() {
  const router = useRouter();
  const { toast } = useToast();
  const review = useOfferReview();
  const [isPending, startTransition] = useTransition();
  const busy = useRef(false);

  function run(offerId: string, offerTitle: string, reviewToken: string): void {
    if (busy.current) return;
    busy.current = true;
    review.beginWrite();
    startTransition(async () => {
      try {
        const result = await settleAction(() => dismissSourceChangeAction(offerId, reviewToken));
        if (result.success) {
          review.recordWrite(result.data.reviewToken, result.data.previousReviewToken);
          toast({ title: 'Source change dismissed', description: offerTitle });
        } else {
          review.recordFailure(result.reason);
          toast({
            variant: 'destructive',
            title: 'Could not dismiss the source change',
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
