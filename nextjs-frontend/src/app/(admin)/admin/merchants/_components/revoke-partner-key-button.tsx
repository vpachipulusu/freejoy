'use client';

import { useRef, useState } from 'react';
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
import { useSubmitOnce } from '../../_components/use-server-form-errors';
import { PARTNER_KEYS_HEADING_ID } from './partner-keys-ids';

type Props = { merchantId: string; keyId: string; label: string };

/**
 * "Revoke" with a confirmation that names the key by its label (never the key). A revoked key stops working on
 * its next request and cannot be restored. Success is announced in a toast (label only) and the page refreshes;
 * a failure keeps the dialog open with the curated message in an alert and allows a retry, because an admin who
 * is revoking a leaked key must not be left believing it worked. Focus returns to the row's button on Cancel,
 * Escape or failure, and goes to the section heading only after a revoke (the button is then gone).
 */
export function RevokePartnerKeyButton({ merchantId, keyId, label }: Props) {
  const router = useRouter();
  const { toast } = useToast();
  const [open, setOpen] = useState(false);
  const [error, setError] = useState('');
  const [isPending, setPending] = useState(false);
  const submitOnce = useSubmitOnce();
  const trigger = useRef<HTMLButtonElement>(null);
  const revoked = useRef(false);

  function onOpenChange(next: boolean): void {
    if (!next && isPending) return;
    if (next) setError('');
    setOpen(next);
  }

  async function revoke(): Promise<void> {
    const result = await settleAction(() => revokePartnerKeyAction(merchantId, keyId));
    if (!result.success) {
      setError(result.error);
      return;
    }
    revoked.current = true;
    toast({ title: 'Key revoked', description: label });
    setOpen(false);
    router.refresh();
  }

  function confirm(): void {
    if (isPending) return;
    setError('');
    setPending(true);
    void submitOnce(revoke).finally(() => setPending(false));
  }

  return (
    <>
      <Button
        ref={trigger}
        type="button"
        variant="outline"
        size="sm"
        className="min-h-11"
        onClick={() => onOpenChange(true)}
      >
        Revoke
        <span className="sr-only"> key {label}</span>
      </Button>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            if (revoked.current) document.getElementById(PARTNER_KEYS_HEADING_ID)?.focus();
            else trigger.current?.focus();
          }}
        >
          <DialogHeader>
            <DialogTitle>Revoke this key?</DialogTitle>
            <DialogDescription>
              The key <span className="font-medium [overflow-wrap:anywhere]">{label}</span> stops
              working on its next request. Any system using it will be refused. This cannot be
              undone: issue a new key instead.
            </DialogDescription>
          </DialogHeader>
          <p role="alert" className="min-h-5 text-sm font-medium text-destructive">
            {error}
          </p>
          <p role="status" className="sr-only">
            {isPending ? 'Revoking the key' : ''}
          </p>
          <DialogFooter className="gap-2">
            <Button
              type="button"
              variant="outline"
              className="min-h-11 aria-disabled:cursor-not-allowed aria-disabled:opacity-50"
              aria-disabled={isPending || undefined}
              onClick={() => onOpenChange(false)}
            >
              Cancel
            </Button>
            <Button
              type="button"
              variant="destructive"
              className="min-h-11 aria-disabled:cursor-not-allowed aria-disabled:opacity-50"
              aria-disabled={isPending || undefined}
              onClick={confirm}
            >
              {isPending ? 'Revoking…' : 'Revoke key'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
