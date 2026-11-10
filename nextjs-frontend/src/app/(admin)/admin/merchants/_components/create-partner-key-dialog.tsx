'use client';

import { useEffect, useRef, useState, useTransition, type FormEvent } from 'react';
import { useRouter } from 'next/navigation';
import { Copy, KeyRound } from 'lucide-react';
import { createPartnerKeyAction } from '@/app/actions/admin/partner-key-actions';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { settleAction } from '@/lib/admin-catalogue/settle-action';
import {
  PARTNER_KEY_LABEL_MAX,
  type IssuedPartnerKeyView,
} from '@/lib/admin-catalogue/partner-key-schema';
import { Field } from '../../_components/form-field';

export const KEY_CREATED_ANNOUNCEMENT =
  'Key created. It is shown only once: copy it now and store it safely.';
export const KEY_COPIED_ANNOUNCEMENT = 'Key copied to the clipboard.';
export const KEY_COPY_FAILED_ANNOUNCEMENT =
  'The key could not be copied automatically. It is selected: copy it yourself.';

type Props = { merchantId: string; merchantName: string };

/**
 * Issues a partner API key. Step one asks for a label; step two shows the key ONCE in a read-only field with a
 * copy button and a polite status announcement. The key lives only in this component's state: it is never put in
 * a toast, a URL, the console or storage, and it is cleared when the dialog closes (by any route). Clicking
 * outside the dialog does not close it while the key is on screen, so the one chance to copy it is not lost by
 * accident; Escape and the close button still work.
 */
export function CreatePartnerKeyDialog({ merchantId, merchantName }: Props) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [label, setLabel] = useState('');
  const [fieldError, setFieldError] = useState<string>();
  const [error, setError] = useState<string>();
  const [issued, setIssued] = useState<IssuedPartnerKeyView | null>(null);
  const [announcement, setAnnouncement] = useState('');
  const [isPending, startTransition] = useTransition();
  const keyInput = useRef<HTMLInputElement>(null);
  const created = useRef(false);

  useEffect(() => {
    if (issued) keyInput.current?.select();
  }, [issued]);

  function reset(): void {
    setLabel('');
    setFieldError(undefined);
    setError(undefined);
    setIssued(null);
    setAnnouncement('');
  }

  function onOpenChange(next: boolean): void {
    setOpen(next);
    if (next) return;
    reset();
    if (created.current) {
      created.current = false;
      router.refresh();
    }
  }

  function submit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    if (isPending) return;
    setFieldError(undefined);
    setError(undefined);
    startTransition(async () => {
      const result = await settleAction(() => createPartnerKeyAction(merchantId, { label }));
      if (result.success) {
        created.current = true;
        setIssued((result as { data: IssuedPartnerKeyView }).data);
        setAnnouncement(KEY_CREATED_ANNOUNCEMENT);
        return;
      }
      const fieldErrors = 'fieldErrors' in result ? result.fieldErrors : undefined;
      setFieldError(fieldErrors?.label);
      setError(fieldErrors?.label ? undefined : result.error);
    });
  }

  async function copy(): Promise<void> {
    if (!issued) return;
    try {
      await navigator.clipboard.writeText(issued.apiKey);
      setAnnouncement(KEY_COPIED_ANNOUNCEMENT);
    } catch {
      keyInput.current?.select();
      setAnnouncement(KEY_COPY_FAILED_ANNOUNCEMENT);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogTrigger asChild>
        <Button type="button">
          <KeyRound aria-hidden="true" className="me-2 size-4" />
          Create key
        </Button>
      </DialogTrigger>
      <DialogContent
        onInteractOutside={(event) => {
          if (issued) event.preventDefault();
        }}
      >
        {issued ? (
          <div className="grid gap-4">
            <DialogHeader>
              <DialogTitle>Your new key</DialogTitle>
              <DialogDescription>
                This key is shown once and cannot be recovered. Copy it now and give it to{' '}
                {merchantName} through a safe channel. If it is lost, revoke it and create another.
              </DialogDescription>
            </DialogHeader>
            <div className="flex gap-2">
              <Input
                ref={keyInput}
                readOnly
                value={issued.apiKey}
                aria-label="New partner API key"
                autoComplete="off"
                spellCheck={false}
                className="font-mono text-xs"
                onFocus={(event) => event.currentTarget.select()}
              />
              <Button type="button" variant="outline" onClick={copy}>
                <Copy aria-hidden="true" className="me-2 size-4" />
                Copy key
              </Button>
            </div>
            <p role="status" className="min-h-5 text-sm text-muted-foreground">
              {announcement}
            </p>
            <DialogFooter>
              <Button type="button" onClick={() => onOpenChange(false)}>
                Done
              </Button>
            </DialogFooter>
          </div>
        ) : (
          <form onSubmit={submit} className="grid gap-4" noValidate>
            <DialogHeader>
              <DialogTitle>Create a partner key</DialogTitle>
              <DialogDescription>
                A key lets a system of {merchantName} call the partner API as this merchant. Give it
                a label that says where it will be used.
              </DialogDescription>
            </DialogHeader>
            <Field
              id="partner-key-label"
              label="Label"
              error={fieldError}
              hint={`Up to ${PARTNER_KEY_LABEL_MAX} characters.`}
              required
            >
              {(aria) => (
                <Input
                  id="partner-key-label"
                  value={label}
                  maxLength={PARTNER_KEY_LABEL_MAX}
                  autoComplete="off"
                  onChange={(event) => setLabel(event.target.value)}
                  {...aria}
                />
              )}
            </Field>
            {error ? (
              <p role="alert" className="text-sm font-medium text-destructive">
                {error}
              </p>
            ) : null}
            <DialogFooter className="gap-2">
              <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
                Cancel
              </Button>
              <Button type="submit" disabled={isPending}>
                {isPending ? 'Creating...' : 'Create key'}
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
