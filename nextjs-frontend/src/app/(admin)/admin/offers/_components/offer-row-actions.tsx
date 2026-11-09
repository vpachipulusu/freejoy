'use client';

import { useRef, useState } from 'react';
import Link from 'next/link';
import { ClipboardCheck, MoreHorizontal, Pencil } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import {
  availableOfferActions,
  OFFER_ACTION_LABELS,
  type OfferAction,
  isReviewedOfferAction,
  type OfferStatus,
} from '@/lib/admin-catalogue/offer-status';
import { ArchiveDialog } from './archive-dialog';
import { useOfferAction } from './use-offer-action';

type OfferRowActionsProps = {
  offerId: string;
  offerTitle: string;
  status: OfferStatus;
};

/**
 * The row menu: Edit, then the lifecycle steps the offer's status allows (Publish or Unpublish, Mark verified,
 * Expire, Archive). The list is a hint: the API refuses anything the status table does not allow. While a step
 * runs the trigger is aria-disabled (it keeps focus) and a polite status says so.
 */
export function OfferRowActions({ offerId, offerTitle, status }: OfferRowActionsProps) {
  const { run, isPending } = useOfferAction();
  const [confirmingArchive, setConfirmingArchive] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  // Publish and Mark verified approve an offer, which needs the whole offer in front of the editor (description,
  // steps, links, source URL). This list shows none of them, so those steps live on the offer's own page.
  const allowed = availableOfferActions(status);
  const actions = allowed.filter((action) => !isReviewedOfferAction(action));
  const canReview = allowed.some(isReviewedOfferAction);

  function choose(action: OfferAction): void {
    if (isPending) return;
    if (action === 'archive') {
      setConfirmingArchive(true);
      return;
    }
    run(offerId, action, offerTitle);
  }

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            ref={triggerRef}
            variant="ghost"
            className="size-11 p-0 hover:bg-accent aria-disabled:opacity-50"
            aria-disabled={isPending || undefined}
          >
            <span className="sr-only">Open menu for {offerTitle}</span>
            <MoreHorizontal aria-hidden="true" className="h-4 w-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuLabel>Actions</DropdownMenuLabel>
          <DropdownMenuSeparator />
          <DropdownMenuItem asChild className="min-h-11">
            <Link href={`/admin/offers/${offerId}`} className="flex items-center">
              <Pencil aria-hidden="true" className="mr-2 h-4 w-4" />
              Edit
            </Link>
          </DropdownMenuItem>
          {canReview ? (
            <DropdownMenuItem asChild className="min-h-11">
              <Link href={`/admin/offers/${offerId}`} className="flex items-center">
                <ClipboardCheck aria-hidden="true" className="mr-2 h-4 w-4" />
                Review
              </Link>
            </DropdownMenuItem>
          ) : null}
          {actions.length > 0 ? <DropdownMenuSeparator /> : null}
          {actions.map((action) => (
            <DropdownMenuItem
              key={action}
              className={
                action === 'archive'
                  ? 'min-h-11 text-destructive focus:text-destructive'
                  : 'min-h-11'
              }
              onSelect={() => choose(action)}
            >
              {OFFER_ACTION_LABELS[action]}
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
      <span role="status" className="sr-only">
        {isPending ? `Updating ${offerTitle}` : ''}
      </span>
      <ArchiveDialog
        open={confirmingArchive}
        onOpenChange={setConfirmingArchive}
        offerTitle={offerTitle}
        onConfirm={() => run(offerId, 'archive', offerTitle)}
        returnFocusTo={triggerRef}
      />
    </>
  );
}
