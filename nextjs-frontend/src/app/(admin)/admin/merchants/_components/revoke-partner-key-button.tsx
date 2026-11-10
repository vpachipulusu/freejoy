'use client';

import { useRef, useState, useTransition } from 'react';
import { useRouter } from 'next/navigation';
import { revokePartnerKeyAction } from '@/app/actions/admin/partner-key-actions';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { settleAction } from '@/lib/admin-catalogue/settle-action';
import { useToast } from '@/hooks/use-toast';

/** The heading of the keys section: where focus goes when the dialog closes (the row's button may be gone). */
export const PARTNER_KEYS_HEADING_ID = 'partner-keys-heading';

type Props = { merchantId: string; keyId: string; label: string };

/**
 * "Revoke" with a confirmation that names the key by its label (never the key). A revoked key stops working on
 * its next request and cannot be restored. The outcome is announced in a toast (label only) and the page refreshes.
 */
export function RevokePartnerKeyButton({ merchantId, keyId, label }: Props) {
  const router = useRouter();
  const { toast } = useToast();
  const [open, setOpen] = useState(false);
  const [isPending, startTransition] = useTransition();
  const busy = useRef(false);

  function confirm(): void {
    if (busy.current) return;
    busy.current = true;
    startTransition(async () => {
      try {
        const result = await settleAction(() => revokePartnerKeyAction(merchantId, keyId));
        if (result.success) {
          toast({ title: 'Key revoked', description: label });
        } else {
          toast({
            variant: 'destructive',
            title: 'Could not revoke the key',
            description: result.error,
          });
        }
        setOpen(false);
        router.refresh();
      } finally {
        busy.current = false;
      }
    });
  }

  return (
    <>
      <Button type="button" variant="outline" size="sm" onClick={() => setOpen(true)}>
        Revoke
        <span className="sr-only"> key {label}</span>
      </Button>
      <Dialog open={open} onOpenChange={(next) => !isPending && setOpen(next)}>
        <DialogContent
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            document.getElementById(PARTNER_KEYS_HEADING_ID)?.focus();
          }}
        >
          <DialogHeader>
            <DialogTitle>Revoke this key?</DialogTitle>
            <DialogDescription>
              The key <span className="break-words font-medium">{label}</span> stops working on its
              next request. Any system using it will be refused. This cannot be undone: issue a new
              key instead.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="gap-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => setOpen(false)}
              disabled={isPending}
            >
              Cancel
            </Button>
            <Button type="button" variant="destructive" onClick={confirm} disabled={isPending}>
              {isPending ? 'Revoking...' : 'Revoke key'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
