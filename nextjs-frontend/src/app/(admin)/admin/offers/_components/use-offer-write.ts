'use client';

import { useRef, useTransition } from 'react';
import { useRouter } from 'next/navigation';
import { settleAction } from '@/lib/admin-catalogue/settle-action';
import { useToast } from '@/hooks/use-toast';
import { useOfferReview } from './offer-review-context';

/** What an offer write's server action answers with, as far as this hook needs it. */
type OfferWriteResult =
  | { success: true; data: { reviewToken?: string; previousReviewToken?: string } }
  | { success: false; error: string; reason?: 'offer_changed' };

export type OfferWriteRequest = {
  /** The server action call. A call that throws is turned into the curated failure. */
  call: () => Promise<OfferWriteResult>;
  successTitle: string;
  failureTitle: string;
  /** The toast text under the title of a success: the offer's title. */
  description: string;
  /** Told the curated message of a failure, for a control that keeps it on the page after the toast is gone. */
  onFailure?: (message: string) => void;
};

/**
 * The one way an offer page runs a write through a server action: the review state is told the write began and
 * how it ended (a stale token raises the lasting "changed since you opened it" notice), a toast announces the
 * outcome and `router.refresh()` loads the result (after a refused "changed since you opened it" it loads the new
 * content too). Only one write runs at a time: a second request while one is in flight is ignored (the controls
 * are aria-disabled, which keeps focus, so the guard is here as well). Used by the lifecycle steps
 * (`useOfferAction`) and by dismissing a source change (`useDismissSourceChange`).
 */
export function useOfferWrite() {
  const router = useRouter();
  const { toast } = useToast();
  const review = useOfferReview();
  const [isPending, startTransition] = useTransition();
  const busy = useRef(false);

  function run({
    call,
    successTitle,
    failureTitle,
    description,
    onFailure,
  }: OfferWriteRequest): void {
    if (busy.current) return;
    busy.current = true;
    review.beginWrite();
    startTransition(async () => {
      try {
        const result = await settleAction(call);
        if (result.success) {
          review.recordWrite(result.data.reviewToken, result.data.previousReviewToken);
          toast({ title: successTitle, description });
        } else {
          review.recordFailure(result.reason);
          onFailure?.(result.error);
          toast({ variant: 'destructive', title: failureTitle, description: result.error });
        }
        router.refresh();
      } finally {
        busy.current = false;
      }
    });
  }

  return { run, isPending };
}
