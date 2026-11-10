'use client';

import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import {
  availableOfferActions,
  OFFER_ACTION_LABELS,
  type OfferAction,
  type OfferRowStatus,
} from '@/lib/admin-catalogue/offer-status';
import { useFormBusy, useFormDirty } from '../../_components/dirty-form-context';
import { ArchiveDialog } from './archive-dialog';
import { ARIA_DISABLED_STYLE } from './disabled-style';
import { useOfferReview, useReportLoadedToken } from './offer-review-context';
import { useOfferAction } from './use-offer-action';

type OfferLifecycleButtonsProps = {
  offerId: string;
  offerTitle: string;
  status: OfferRowStatus;
  /** The review token of the offer as this page loaded it: Publish and Mark verified send it back. */
  reviewToken: string;
};

const CHANGED_NOTICE =
  'This offer changed since you opened it. This page now shows the latest version: review it again.';

/**
 * The lifecycle steps for the edit page: the same set as the row menu, as visible buttons. They act on the saved
 * offer, so while the form beside them holds unsaved edits, or a save is still being reloaded into the page (the
 * token they hold would be stale), they wait (aria-disabled, with the reason written out when it is the edits).
 * While a step runs they are aria-disabled too, which keeps focus on the pressed button.
 *
 * When the offer changed under the editor a lasting notice says so (a toast is gone after a few seconds). When
 * the button that was pressed is gone after the reload (Publish on a published offer) focus moves into the group.
 */
export function OfferLifecycleButtons({
  offerId,
  offerTitle,
  status,
  reviewToken,
}: OfferLifecycleButtonsProps) {
  const { run, isPending } = useOfferAction();
  const dirty = useFormDirty();
  const reloading = useFormBusy();
  const { changed } = useOfferReview();
  useReportLoadedToken(reviewToken);
  const [confirmingArchive, setConfirmingArchive] = useState(false);
  const archiveRef = useRef<HTMLButtonElement>(null);
  const groupRef = useRef<HTMLDivElement>(null);
  const noStepsRef = useRef<HTMLParagraphElement>(null);
  const keepFocusHere = useRef(false);
  const actions = availableOfferActions(status);
  const blocked = isPending || dirty || reloading;

  useEffect(() => {
    if (!keepFocusHere.current) return;
    keepFocusHere.current = false;
    const group = groupRef.current;
    const target = group ?? noStepsRef.current;
    if (!target || target.contains(document.activeElement)) return;
    (group?.querySelector('button') ?? target).focus();
  }, [status, reviewToken]);

  const notice = changed ? (
    <p role="alert" className="text-sm font-medium text-destructive">
      {CHANGED_NOTICE}
    </p>
  ) : null;

  if (actions.length === 0) {
    return (
      <>
        {notice}
        <p ref={noStepsRef} tabIndex={-1} className="text-sm text-muted-foreground">
          {status === 'ARCHIVED'
            ? 'This offer is archived and cannot be changed.'
            : 'This offer has a status this page does not know, so it cannot be changed here.'}
        </p>
      </>
    );
  }

  function start(action: OfferAction): void {
    keepFocusHere.current = true;
    run(offerId, action, offerTitle, reviewToken);
  }

  function choose(action: OfferAction): void {
    if (blocked) return;
    if (action === 'archive') setConfirmingArchive(true);
    else start(action);
  }

  return (
    <>
      {notice}
      <div
        ref={groupRef}
        role="group"
        aria-label="Offer status actions"
        className="flex flex-wrap gap-2"
        onBlur={(event) => {
          // Focus moved on to something else on purpose: do not pull it back later.
          const next = event.relatedTarget;
          if (next instanceof Node && !event.currentTarget.contains(next)) {
            keepFocusHere.current = false;
          }
        }}
      >
        {actions.map((action) => (
          <Button
            key={action}
            ref={action === 'archive' ? archiveRef : undefined}
            type="button"
            variant={action === 'publish' ? 'default' : 'outline'}
            className={ARIA_DISABLED_STYLE}
            aria-disabled={blocked || undefined}
            onClick={() => choose(action)}
          >
            {OFFER_ACTION_LABELS[action]}
          </Button>
        ))}
      </div>
      <p className="text-sm text-muted-foreground" role="status">
        {isPending
          ? `Updating ${offerTitle}`
          : dirty
            ? 'Save or discard your changes first: these steps use the saved version of the offer.'
            : reloading
              ? 'Loading the saved version of the offer.'
              : ''}
      </p>
      <ArchiveDialog
        open={confirmingArchive}
        onOpenChange={setConfirmingArchive}
        offerTitle={offerTitle}
        onConfirm={() => start('archive')}
        returnFocusTo={archiveRef}
      />
    </>
  );
}
