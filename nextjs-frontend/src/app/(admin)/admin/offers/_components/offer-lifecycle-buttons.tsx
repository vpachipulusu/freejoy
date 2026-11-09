'use client';

import { useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import {
  availableOfferActions,
  OFFER_ACTION_LABELS,
  type OfferAction,
  type OfferRowStatus,
} from '@/lib/admin-catalogue/offer-status';
import { useFormDirty } from '../../_components/dirty-form-context';
import { ArchiveDialog } from './archive-dialog';
import { useOfferAction } from './use-offer-action';

type OfferLifecycleButtonsProps = {
  offerId: string;
  offerTitle: string;
  status: OfferRowStatus;
  reviewToken: string;
};

const DISABLED_STYLE = 'aria-disabled:cursor-not-allowed aria-disabled:opacity-50';

/**
 * The lifecycle steps for the edit page: the same set as the row menu, as visible buttons. They act on the saved
 * offer, so while the form beside them holds unsaved edits they wait (aria-disabled, with the reason written
 * out). While a step runs they are aria-disabled too, which keeps focus on the pressed button.
 */
export function OfferLifecycleButtons({
  offerId,
  offerTitle,
  status,
  reviewToken,
}: OfferLifecycleButtonsProps) {
  const { run, isPending } = useOfferAction();
  const dirty = useFormDirty();
  const [confirmingArchive, setConfirmingArchive] = useState(false);
  const archiveRef = useRef<HTMLButtonElement>(null);
  const actions = availableOfferActions(status);
  const blocked = isPending || dirty;

  if (actions.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        {status === 'ARCHIVED'
          ? 'This offer is archived and cannot be changed.'
          : 'This offer has a status this page does not know, so it cannot be changed here.'}
      </p>
    );
  }

  function choose(action: OfferAction): void {
    if (blocked) return;
    if (action === 'archive') setConfirmingArchive(true);
    else run(offerId, action, offerTitle, reviewToken);
  }

  return (
    <>
      <div role="group" aria-label="Offer status actions" className="flex flex-wrap gap-2">
        {actions.map((action) => (
          <Button
            key={action}
            ref={action === 'archive' ? archiveRef : undefined}
            type="button"
            variant={action === 'publish' ? 'default' : 'outline'}
            className={DISABLED_STYLE}
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
            : ''}
      </p>
      <ArchiveDialog
        open={confirmingArchive}
        onOpenChange={setConfirmingArchive}
        offerTitle={offerTitle}
        onConfirm={() => run(offerId, 'archive', offerTitle, reviewToken)}
        returnFocusTo={archiveRef}
      />
    </>
  );
}
