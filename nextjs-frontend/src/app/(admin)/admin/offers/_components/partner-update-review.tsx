'use client';

import { useEffect, useId, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import type { ProposalRow } from '@/lib/admin-catalogue/partner-proposal';
import { useFormBusy, useFormDirty } from '../../_components/dirty-form-context';
import { ARIA_DISABLED_STYLE } from './disabled-style';
import { useOfferReview } from './offer-review-context';
import { PartnerConfirmDialog } from './partner-confirm-dialog';
import { ChangedFields, NotAppliedNotes } from './partner-proposal-list';
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

type Pending = { kind: 'apply' | 'dismiss'; hash: string };

const NOT_ACTIONABLE_NOTICE =
  'This offer can no longer be changed here, so the retailer update cannot be applied. You can still dismiss it.';
const CHANGED_NOTICE =
  "The offer or the retailer's update changed while you were looking. This is the latest: compare it again.";
const DIRTY_NOTICE =
  'Save or discard your changes first: applying uses the saved version of the offer.';
const RELOADING_NOTICE = 'Loading the saved version of the offer.';
const WRITING_NOTICE = 'Another change is being saved. Wait for it to finish.';

/**
 * The retailer's proposal beside the current content, and the two things an editor can do with it: apply it
 * (through the normal edit path) or dismiss it. Each changed field is a group with two labelled regions, Current
 * and Proposed, that stack below 640px, so the comparison reads the same on a phone and with a screen reader; a
 * field that did not change is only named. Dismissing, and applying to a live offer (which changes the public page
 * at once), ask first; applying to a draft is one press.
 *
 * The buttons act on the saved offer, so they wait (aria-disabled, which keeps focus, with the reason attached)
 * while the form beside them holds unsaved edits, while a save reloads and while any write on the page runs. A
 * failure stays on the page as an alert, because a toast is gone after a few seconds.
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
  const { apply, dismiss, isPending, failure } = usePartnerUpdate();
  const dirty = useFormDirty();
  const reloading = useFormBusy();
  const { changed: offerChanged, writing } = useOfferReview();
  const [confirming, setConfirming] = useState<Pending | null>(null);
  const [attempted, setAttempted] = useState(false);
  const applyRef = useRef<HTMLButtonElement>(null);
  const dismissRef = useRef<HTMLButtonElement>(null);
  const doneRef = useRef<HTMLParagraphElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const keepFocusHere = useRef(false);
  const statusId = useId();
  const waiting = pendingHash !== null;
  const blocked = isPending || writing || dirty || reloading;
  const changed = rows.filter((row) => row.changed);
  const unchanged = rows.filter((row) => !row.changed && row.note === undefined);
  // The confirmation belongs to the proposal it was opened for: a different one closes it.
  const open = (kind: Pending['kind']): boolean =>
    confirming !== null && confirming.kind === kind && confirming.hash === pendingHash;

  useEffect(() => {
    if (!keepFocusHere.current) return;
    keepFocusHere.current = false;
    const container = containerRef.current;
    if (!container || container.contains(document.activeElement)) return;
    (applyRef.current ?? dismissRef.current ?? doneRef.current)?.focus();
  }, [reviewToken, waiting]);

  useEffect(() => {
    // A refused write leaves the buttons where they are: nothing is left to move focus back to later.
    if (failure !== null) keepFocusHere.current = false;
  }, [failure]);

  function send(kind: Pending['kind']): void {
    setConfirming(null);
    if (blocked || pendingHash === null) return;
    keepFocusHere.current = true;
    setAttempted(true);
    (kind === 'apply' ? apply : dismiss)(offerId, offerTitle, reviewToken, pendingHash);
  }

  function ask(kind: Pending['kind']): void {
    if (blocked || pendingHash === null) return;
    if (kind === 'apply' && !live) send('apply');
    else setConfirming({ kind, hash: pendingHash });
  }

  if (!waiting && !attempted) return null;

  const statusText = isPending
    ? `Updating ${offerTitle}`
    : writing
      ? WRITING_NOTICE
      : dirty
        ? DIRTY_NOTICE
        : reloading
          ? RELOADING_NOTICE
          : '';
  const heldProps = {
    className: ARIA_DISABLED_STYLE,
    'aria-disabled': blocked || undefined,
    'aria-describedby': blocked ? statusId : undefined,
  };

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
      {offerChanged ? <p className="text-sm font-medium">{CHANGED_NOTICE}</p> : null}
      {waiting ? (
        <>
          <p className="text-sm text-muted-foreground">
            The retailer sent an update on {sentOn || 'an unknown date'}. The offer
            {live ? ' and its published page have' : ' has'} not changed. Compare the two, then
            apply the update or dismiss it. An offer that has already ended keeps its end date
            unless the update gives a new one in the future.
          </p>
          {live && actionable ? (
            <p className="text-sm font-medium">
              Applying changes the published page straight away.
            </p>
          ) : null}
          {changed.length === 0 ? (
            <p className="text-sm">
              Nothing in the update differs from the offer as it is now. Dismiss it to clear the
              review queue.
            </p>
          ) : (
            <ChangedFields rows={changed} />
          )}
          <NotAppliedNotes rows={rows} />
          {unchanged.length > 0 ? (
            <p className="text-sm text-muted-foreground">
              Not changed: {unchanged.map((row) => row.label).join(', ')}.
            </p>
          ) : null}
          {actionable ? null : (
            <p className="text-sm text-muted-foreground">{NOT_ACTIONABLE_NOTICE}</p>
          )}
          {failure === null ? null : (
            <p role="alert" className="text-sm font-medium text-destructive">
              {failure}
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            {actionable ? (
              <Button ref={applyRef} type="button" {...heldProps} onClick={() => ask('apply')}>
                {live ? 'Apply to the published offer' : 'Apply retailer update'}
              </Button>
            ) : null}
            <Button
              ref={dismissRef}
              type="button"
              variant="outline"
              {...heldProps}
              onClick={() => ask('dismiss')}
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
      <p id={statusId} className="text-sm text-muted-foreground" role="status">
        {statusText}
      </p>
      <PartnerConfirmDialog
        open={open('apply')}
        onOpenChange={(next) => !next && setConfirming(null)}
        title="Apply this retailer update to the published offer?"
        description={
          <>
            This changes the published page for{' '}
            <span className="font-medium [overflow-wrap:anywhere]">{offerTitle}</span> straight away
            {changed.length > 0 ? `. Changed: ${changed.map((row) => row.label).join(', ')}` : ''}.
          </>
        }
        confirmLabel="Apply update"
        onConfirm={() => send('apply')}
        onReturnFocus={() => applyRef.current?.focus()}
      />
      <PartnerConfirmDialog
        open={open('dismiss')}
        onOpenChange={(next) => !next && setConfirming(null)}
        title="Dismiss this retailer update?"
        description={
          <>
            The update the retailer sent for{' '}
            <span className="font-medium [overflow-wrap:anywhere]">{offerTitle}</span> will be
            forgotten and the offer leaves the review queue. Nothing on the offer is edited. If the
            retailer sends different content later, it will be queued again.
          </>
        }
        confirmLabel="Dismiss update"
        onConfirm={() => send('dismiss')}
        onReturnFocus={() => (dismissRef.current ?? applyRef.current)?.focus()}
      />
    </section>
  );
}
