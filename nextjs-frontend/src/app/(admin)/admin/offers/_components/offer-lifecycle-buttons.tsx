'use client';

import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { COMMERCIAL_BASIS_HELP, commercialBasisLabel } from '@/lib/admin-catalogue/offer-labels';
import { isCommercialBasis } from '@/lib/admin-catalogue/offer-schema';
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
  /** The commercial basis as saved. Publish sends it back when the editor has to confirm it. */
  commercialBasis?: string;
  /**
   * True when the offer's only active links go straight to the merchant: the API then refuses to publish until the
   * editor confirms the basis, so this page asks for that confirmation (a hint; the API applies the rule).
   */
  confirmBasisToPublish?: boolean;
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
  commercialBasis,
  confirmBasisToPublish = false,
}: OfferLifecycleButtonsProps) {
  const { run, isPending } = useOfferAction();
  const dirty = useFormDirty();
  const reloading = useFormBusy();
  const { changed } = useOfferReview();
  useReportLoadedToken(reviewToken);
  const [confirmingArchive, setConfirmingArchive] = useState(false);
  // Tied to the token it was given for: when the offer moves on, the editor confirms again.
  const [confirmedFor, setConfirmedFor] = useState<string | null>(null);
  const archiveRef = useRef<HTMLButtonElement>(null);
  const groupRef = useRef<HTMLDivElement>(null);
  const noStepsRef = useRef<HTMLParagraphElement>(null);
  const keepFocusHere = useRef(false);
  const actions = availableOfferActions(status);
  const blocked = isPending || dirty || reloading;
  const basisToConfirm =
    confirmBasisToPublish && isCommercialBasis(commercialBasis) ? commercialBasis : null;
  const awaitingBasis = basisToConfirm !== null && confirmedFor !== reviewToken;

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
    run(
      offerId,
      action,
      offerTitle,
      reviewToken,
      action === 'publish' ? (basisToConfirm ?? undefined) : undefined
    );
  }

  function isHeld(action: OfferAction): boolean {
    return blocked || (action === 'publish' && awaitingBasis);
  }

  function choose(action: OfferAction): void {
    if (isHeld(action)) return;
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
            aria-disabled={isHeld(action) || undefined}
            onClick={() => choose(action)}
          >
            {OFFER_ACTION_LABELS[action]}
          </Button>
        ))}
      </div>
      {basisToConfirm !== null && actions.includes('publish') ? (
        <label className="flex min-h-11 items-start gap-3 text-sm">
          <input
            type="checkbox"
            className="mt-0.5 size-5 shrink-0 accent-[hsl(var(--primary))]"
            checked={!awaitingBasis}
            onChange={(event) => setConfirmedFor(event.target.checked ? reviewToken : null)}
          />
          <span>
            <span className="block font-medium">
              I confirm this listing&apos;s commercial basis is{' '}
              {commercialBasisLabel(basisToConfirm)}
            </span>
            <span className="block text-muted-foreground">
              {COMMERCIAL_BASIS_HELP[basisToConfirm]} Its only active links go straight to the
              merchant, so nothing else says how it is paid for. If this is wrong, change it in the
              form below and save first.
            </span>
          </span>
        </label>
      ) : null}
      <p className="text-sm text-muted-foreground" role="status">
        {isPending
          ? `Updating ${offerTitle}`
          : dirty
            ? 'Save or discard your changes first: these steps use the saved version of the offer.'
            : reloading
              ? 'Loading the saved version of the offer.'
              : awaitingBasis && actions.includes('publish')
                ? 'Confirm the commercial basis above to publish.'
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
