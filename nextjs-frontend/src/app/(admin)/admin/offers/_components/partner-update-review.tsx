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
import type { ProposalRow } from '@/lib/admin-catalogue/partner-proposal';
import { useFormBusy, useFormDirty } from '../../_components/dirty-form-context';
import { ARIA_DISABLED_STYLE } from './disabled-style';
import { usePartnerUpdate } from './use-partner-update';

type PartnerUpdateReviewProps = {
  offerId: string;
  offerTitle: string;
  /** The review token of the offer as this page loaded it. */
  reviewToken: string;
  /** The hash of the proposal this page shows, or null when nothing is waiting. */
  pendingHash: string | null;
  /** When the retailer sent it, already formatted. */
  sentOn: string;
  rows: ProposalRow[];
  /** Whether the offer's status allows a change at all (an archived offer, or an unknown status, does not). */
  actionable: boolean;
  /** A live offer: applying changes what visitors read. */
  live: boolean;
};

const NOT_ACTIONABLE_NOTICE =
  'This offer can no longer be changed here, so the retailer update cannot be applied. You can still dismiss it.';

function Lines({ lines }: { lines: string[] }) {
  return (
    <ul className="space-y-1">
      {lines.map((line, index) => (
        <li key={`${index}-${line}`} className="whitespace-pre-wrap break-words">
          {line}
        </li>
      ))}
    </ul>
  );
}

/**
 * The retailer's proposal beside the current content, and the two things an editor can do with it: apply it
 * (through the normal edit path) or dismiss it (after a confirmation). Each changed field is a group with two
 * labelled regions, Current and Proposed, that stack below 640px, so the comparison reads the same on a phone and
 * with a screen reader; a field that did not change is only named. The buttons act on the saved offer, so they
 * wait (aria-disabled, which keeps focus) while the form beside them holds unsaved edits, while a save reloads and
 * while a write runs.
 *
 * The component stays mounted when nothing is waiting any more: after a write the buttons disappear, so a stable
 * paragraph takes their place and receives the focus the button held (otherwise focus would fall to the page body).
 */
export function PartnerUpdateReview({
  offerId,
  offerTitle,
  reviewToken,
  pendingHash,
  sentOn,
  rows,
  actionable,
  live,
}: PartnerUpdateReviewProps) {
  const { apply, dismiss, isPending } = usePartnerUpdate();
  const dirty = useFormDirty();
  const reloading = useFormBusy();
  const [confirming, setConfirming] = useState(false);
  const [attempted, setAttempted] = useState(false);
  const applyRef = useRef<HTMLButtonElement>(null);
  const dismissRef = useRef<HTMLButtonElement>(null);
  const doneRef = useRef<HTMLParagraphElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const keepFocusHere = useRef(false);
  const waiting = pendingHash !== null;
  const blocked = isPending || dirty || reloading;
  const changed = rows.filter((row) => row.changed);
  const unchanged = rows.filter((row) => !row.changed);

  useEffect(() => {
    if (!keepFocusHere.current) return;
    keepFocusHere.current = false;
    const container = containerRef.current;
    if (!container || container.contains(document.activeElement)) return;
    (applyRef.current ?? dismissRef.current ?? doneRef.current)?.focus();
  }, [reviewToken, waiting]);

  function onApply(): void {
    if (blocked || pendingHash === null) return;
    keepFocusHere.current = true;
    setAttempted(true);
    apply(offerId, offerTitle, reviewToken, pendingHash);
  }

  function onConfirmDismiss(): void {
    setConfirming(false);
    if (pendingHash === null) return;
    keepFocusHere.current = true;
    setAttempted(true);
    dismiss(offerId, offerTitle, reviewToken, pendingHash);
  }

  if (!waiting && !attempted) return null;

  return (
    <section
      ref={containerRef}
      aria-labelledby="partner-update-heading"
      className="space-y-3"
      onBlur={(event) => {
        // Focus moved on to something else on purpose: do not pull it back later.
        const next = event.relatedTarget;
        if (next instanceof Node && !event.currentTarget.contains(next)) {
          keepFocusHere.current = false;
        }
      }}
    >
      <h2 id="partner-update-heading" className="text-base font-semibold">
        Retailer update
      </h2>
      {waiting ? (
        <>
          <p className="text-sm text-muted-foreground">
            The retailer sent an update on {sentOn || 'an unknown date'}. The offer
            {live ? ' and its published page have' : ' has'} not changed. Compare the two, then
            apply the update or dismiss it. An offer that has already ended keeps its end date
            unless the update gives a new one in the future.
          </p>
          {changed.length === 0 ? (
            <p className="text-sm">
              Nothing in the update differs from the offer as it is now. Dismiss it to clear the
              review queue.
            </p>
          ) : (
            <ul className="space-y-3">
              {changed.map((row) => (
                <li key={row.key} className="space-y-2 rounded-lg border p-3 text-sm">
                  <h3 className="font-medium">{row.label}</h3>
                  <div className="grid gap-3 sm:grid-cols-2">
                    <div role="group" aria-label={`${row.label}, current`} className="space-y-1">
                      <p className="text-xs font-medium uppercase text-muted-foreground">Current</p>
                      <Lines lines={row.current} />
                    </div>
                    <div role="group" aria-label={`${row.label}, proposed`} className="space-y-1">
                      <p className="text-xs font-medium uppercase text-muted-foreground">
                        Proposed
                      </p>
                      <Lines lines={row.proposed} />
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
          {unchanged.length > 0 ? (
            <p className="text-sm text-muted-foreground">
              Not changed: {unchanged.map((row) => row.label).join(', ')}.
            </p>
          ) : null}
          {actionable ? null : (
            <p className="text-sm text-muted-foreground">{NOT_ACTIONABLE_NOTICE}</p>
          )}
          <div className="flex flex-wrap gap-2">
            {actionable ? (
              <Button
                ref={applyRef}
                type="button"
                className={ARIA_DISABLED_STYLE}
                aria-disabled={blocked || undefined}
                onClick={onApply}
              >
                Apply retailer update
              </Button>
            ) : null}
            <Button
              ref={dismissRef}
              type="button"
              variant="outline"
              className={ARIA_DISABLED_STYLE}
              aria-disabled={blocked || undefined}
              onClick={() => {
                if (!blocked) setConfirming(true);
              }}
            >
              Dismiss retailer update
            </Button>
          </div>
        </>
      ) : (
        <p ref={doneRef} tabIndex={-1} className="text-sm text-muted-foreground">
          The retailer update has been dealt with.
        </p>
      )}
      <p className="text-sm text-muted-foreground" role="status">
        {isPending ? `Updating ${offerTitle}` : ''}
      </p>
      <Dialog open={confirming} onOpenChange={setConfirming}>
        <DialogContent
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            (dismissRef.current ?? applyRef.current)?.focus();
          }}
        >
          <DialogHeader>
            <DialogTitle>Dismiss this retailer update?</DialogTitle>
            <DialogDescription>
              The update the retailer sent for{' '}
              <span className="break-words font-medium">{offerTitle}</span> will be forgotten and
              the offer leaves the review queue. Nothing on the offer is edited. If the retailer
              sends different content later, it will be queued again.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="gap-2">
            <Button type="button" variant="outline" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
            <Button type="button" onClick={onConfirmDismiss}>
              Dismiss update
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}
