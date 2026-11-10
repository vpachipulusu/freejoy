'use client';

import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { useFormBusy, useFormDirty } from '../../_components/dirty-form-context';
import { ARIA_DISABLED_STYLE } from './disabled-style';
import { useDismissSourceChange } from './use-dismiss-source-change';

type DismissSourceChangeButtonProps = {
  offerId: string;
  offerTitle: string;
  /** The review token of the offer as this page loaded it. */
  reviewToken: string;
  /** The `source_changed_at` the page loaded (null: the source has not moved on, or it was dismissed). */
  sourceChangedAt: string | null;
  /** Whether the offer's status allows any step at all (an archived offer, or an unknown status, does not). */
  actionable: boolean;
};

const NOT_ACTIONABLE_NOTICE =
  'This offer can no longer be changed here, so its source change cannot be dismissed.';

/**
 * "Dismiss source change": says the admin has seen that the network's source moved on and keeps the offer as it
 * is. It asks first (the offer leaves the review queue until the source changes again). Like the lifecycle
 * buttons it acts on the saved offer, so it waits (aria-disabled, which keeps focus) while the form beside it
 * holds unsaved edits, while a save is reloading and while it runs; the lifecycle group already says why, so this
 * component's live region only announces that it is working.
 *
 * The component stays mounted when there is nothing left to dismiss: after a successful dismissal the button
 * disappears, so a stable paragraph takes its place and receives the focus the button held (otherwise focus
 * would fall to the page body).
 */
export function DismissSourceChangeButton({
  offerId,
  offerTitle,
  reviewToken,
  sourceChangedAt,
  actionable,
}: DismissSourceChangeButtonProps) {
  const { run, isPending } = useDismissSourceChange();
  const dirty = useFormDirty();
  const reloading = useFormBusy();
  const [confirming, setConfirming] = useState(false);
  const [attempted, setAttempted] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const doneRef = useRef<HTMLParagraphElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const keepFocusHere = useRef(false);
  const marked = sourceChangedAt !== null;
  const canDismiss = marked && actionable;
  const blocked = isPending || dirty || reloading;

  useEffect(() => {
    if (!keepFocusHere.current) return;
    keepFocusHere.current = false;
    const container = containerRef.current;
    if (!container || container.contains(document.activeElement)) return;
    (buttonRef.current ?? doneRef.current)?.focus();
  }, [reviewToken, marked]);

  function confirm(): void {
    setConfirming(false);
    if (sourceChangedAt === null) return;
    keepFocusHere.current = true;
    setAttempted(true);
    run(offerId, offerTitle, reviewToken, sourceChangedAt);
  }

  const doneText = marked
    ? NOT_ACTIONABLE_NOTICE
    : attempted
      ? 'Source change dismissed.'
      : 'Source changed: No';

  return (
    <div
      ref={containerRef}
      className="space-y-2"
      onBlur={(event) => {
        // Focus moved on to something else on purpose: do not pull it back later.
        const next = event.relatedTarget;
        if (next instanceof Node && !event.currentTarget.contains(next)) {
          keepFocusHere.current = false;
        }
      }}
    >
      {canDismiss ? (
        <Button
          ref={buttonRef}
          type="button"
          variant="outline"
          className={ARIA_DISABLED_STYLE}
          aria-disabled={blocked || undefined}
          onClick={() => {
            if (!blocked) setConfirming(true);
          }}
        >
          Dismiss source change
        </Button>
      ) : (
        <p ref={doneRef} tabIndex={-1} className="text-sm text-muted-foreground">
          {doneText}
        </p>
      )}
      <p className="text-sm text-muted-foreground" role="status">
        {isPending ? `Updating ${offerTitle}` : ''}
      </p>
      <Dialog open={confirming} onOpenChange={setConfirming}>
        <DialogContent
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            buttonRef.current?.focus();
          }}
        >
          <DialogHeader>
            <DialogTitle>Dismiss this source change?</DialogTitle>
            <DialogDescription>
              The source of <span className="break-words font-medium">{offerTitle}</span> has
              changed since it was imported. Dismissing says you have seen the change and keeps the
              offer as it is. The offer leaves the review queue unless the source changes again.
              Nothing on the offer is edited.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="gap-2">
            <Button type="button" variant="outline" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
            <Button type="button" onClick={confirm}>
              Dismiss change
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
