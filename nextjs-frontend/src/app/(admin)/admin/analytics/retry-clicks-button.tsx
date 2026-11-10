'use client';

import { useTransition } from 'react';
import { useRouter } from 'next/navigation';
import { Button } from '@/components/ui/button';
import { StatusAnnouncer } from './status-announcer';

export const RETRY_PENDING_MESSAGE = 'Retrying the click report…';

/**
 * Retry for a failed report. A link would reload the page and drop the reader at the top; this refreshes the
 * server data inside a transition, so the button can show that it is working, and moves keyboard focus to the
 * report heading (`focusId`) at once: the error, and this button with it, disappears when the report comes back,
 * which would otherwise drop focus on the page. A second press while it is working does nothing.
 */
export function RetryClicksButton({ focusId }: { focusId: string }) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();

  function retry() {
    if (pending) return;
    document.getElementById(focusId)?.focus();
    startTransition(() => router.refresh());
  }

  return (
    <>
      <Button
        type="button"
        variant="outline"
        onClick={retry}
        aria-disabled={pending || undefined}
        className={pending ? 'opacity-50' : undefined}
      >
        {pending ? 'Retrying…' : 'Retry'}
      </Button>
      <StatusAnnouncer message={pending ? RETRY_PENDING_MESSAGE : ''} />
    </>
  );
}
