'use client';

import { useRef, useState } from 'react';
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
import { useDismissSourceChange } from './use-dismiss-source-change';

type DismissSourceChangeButtonProps = {
  offerId: string;
  offerTitle: string;
  /** The review token of the offer as this page loaded it. */
  reviewToken: string;
};

const DISABLED_STYLE = 'aria-disabled:cursor-not-allowed aria-disabled:opacity-50';

/**
 * "Dismiss source change": says the admin has seen that the network's source moved on and keeps the offer as it
 * is. It asks first (the offer leaves the review queue until the source changes again). Like the lifecycle
 * buttons it acts on the saved offer, so it waits (aria-disabled, which keeps focus) while the form beside it
 * holds unsaved edits, while a save is reloading and while it runs.
 */
export function DismissSourceChangeButton({
  offerId,
  offerTitle,
  reviewToken,
}: DismissSourceChangeButtonProps) {
  const { run, isPending } = useDismissSourceChange();
  const dirty = useFormDirty();
  const reloading = useFormBusy();
  const [confirming, setConfirming] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const blocked = isPending || dirty || reloading;

  return (
    <>
      <Button
        ref={buttonRef}
        type="button"
        variant="outline"
        className={DISABLED_STYLE}
        aria-disabled={blocked || undefined}
        onClick={() => {
          if (!blocked) setConfirming(true);
        }}
      >
        Dismiss source change
      </Button>
      <p className="text-sm text-muted-foreground" role="status">
        {isPending
          ? `Updating ${offerTitle}`
          : dirty
            ? 'Save or discard your changes first: this step uses the saved version of the offer.'
            : reloading
              ? 'Loading the saved version of the offer.'
              : ''}
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
            <Button
              type="button"
              onClick={() => {
                setConfirming(false);
                run(offerId, offerTitle, reviewToken);
              }}
            >
              Dismiss source change
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
