'use client';

import { useEffect, useRef, useState, type FormEvent } from 'react';
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
import { Label } from '@/components/ui/label';
import { ACTION_FAILED_MESSAGE, settleAction } from '@/lib/admin-catalogue/settle-action';
import {
  PARTNER_KEY_LABEL_MAX,
  type IssuedPartnerKeyView,
} from '@/lib/admin-catalogue/partner-key-schema';
import { Field } from '../../_components/form-field';
import { useSubmitOnce } from '../../_components/use-server-form-errors';

export const KEY_CREATED_ANNOUNCEMENT =
  'Key created. It is shown only once: copy it now and store it safely.';
export const KEY_COPIED_ANNOUNCEMENT = 'Key copied to the clipboard.';
export const KEY_COPY_FAILED_ANNOUNCEMENT =
  'The key could not be copied automatically. It is selected: copy it yourself.';
export const KEY_MAY_EXIST_MESSAGE =
  'The request did not finish, so a key may have been created. Check the list of keys before trying again, and revoke any key you do not hold.';

const KEY_DESCRIPTION_ID = 'partner-key-once';
const NON_BREAKING_SPACE = ' ';

type Props = { merchantId: string; merchantName: string };

/**
 * Issues a partner API key. Step one asks for a label; step two shows the key ONCE in a read-only field with a
 * copy button and a polite status announcement. The key lives only in this component's state: it is never put in
 * a toast, a URL, the console or storage, and it is cleared when the dialog closes. While the request is in flight
 * the dialog cannot be closed (a key nobody sees would be issued), and while the key is on screen neither a click
 * outside nor Escape closes it, so the one chance to copy it is not lost by accident; Done and the close button do.
 */
export function CreatePartnerKeyDialog({ merchantId, merchantName }: Props) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [label, setLabel] = useState('');
  const [fieldError, setFieldError] = useState<string>();
  const [error, setError] = useState('');
  const [issued, setIssued] = useState<IssuedPartnerKeyView | null>(null);
  const [announcement, setAnnouncement] = useState('');
  const [isPending, setPending] = useState(false);
  const submitOnce = useSubmitOnce();
  const labelInput = useRef<HTMLInputElement>(null);
  const keyInput = useRef<HTMLInputElement>(null);
  const created = useRef(false);
  const copies = useRef(0);

  useEffect(() => {
    if (issued) keyInput.current?.focus();
  }, [issued]);

  function reset(): void {
    setLabel('');
    setFieldError(undefined);
    setError('');
    setIssued(null);
    setAnnouncement('');
    copies.current = 0;
  }

  function onOpenChange(next: boolean): void {
    if (!next && isPending) return;
    reset();
    setOpen(next);
    if (!next && created.current) {
      created.current = false;
      router.refresh();
    }
  }

  async function create(): Promise<void> {
    const result = await settleAction(() => createPartnerKeyAction(merchantId, { label }));
    if (result.success) {
      created.current = true;
      setIssued((result as { data: IssuedPartnerKeyView }).data);
      setAnnouncement(KEY_CREATED_ANNOUNCEMENT);
      return;
    }
    const thrown = result.error === ACTION_FAILED_MESSAGE;
    if (thrown) created.current = true; // the key may exist: show it in the list when the dialog closes
    const fieldErrors = 'fieldErrors' in result ? result.fieldErrors : undefined;
    setFieldError(fieldErrors?.label);
    setError(thrown ? KEY_MAY_EXIST_MESSAGE : result.error);
    if (fieldErrors?.label) requestAnimationFrame(() => labelInput.current?.focus());
  }

  function submit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    if (issued || isPending) return;
    setFieldError(undefined);
    setError('');
    setPending(true);
    void submitOnce(create).finally(() => setPending(false));
  }

  async function copy(): Promise<void> {
    if (!issued) return;
    copies.current += 1;
    // The text alternates by a no-break space so a second copy is announced again.
    const mark = copies.current % 2 === 0 ? NON_BREAKING_SPACE : '';
    try {
      await navigator.clipboard.writeText(issued.apiKey);
      setAnnouncement(`${KEY_COPIED_ANNOUNCEMENT}${mark}`);
    } catch {
      keyInput.current?.focus();
      keyInput.current?.select();
      setAnnouncement(`${KEY_COPY_FAILED_ANNOUNCEMENT}${mark}`);
    }
  }

  const blocked = issued !== null || isPending;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogTrigger asChild>
        <Button type="button">
          <KeyRound aria-hidden="true" className="size-4" />
          Create key
        </Button>
      </DialogTrigger>
      <DialogContent
        onInteractOutside={(event) => {
          if (blocked) event.preventDefault();
        }}
        onEscapeKeyDown={(event) => {
          if (blocked) event.preventDefault();
        }}
      >
        <form onSubmit={submit} className="grid gap-4" noValidate>
          {issued ? (
            <>
              <DialogHeader>
                <DialogTitle>Your new key</DialogTitle>
                <DialogDescription id={KEY_DESCRIPTION_ID}>
                  This key is shown once and cannot be recovered. Copy it now and give it to{' '}
                  {merchantName} through a safe channel. If it is lost, revoke it and create
                  another.
                </DialogDescription>
              </DialogHeader>
              <div className="grid gap-2">
                <Label htmlFor="partner-key-value">New partner API key</Label>
                <div className="flex flex-col gap-2 sm:flex-row">
                  <Input
                    id="partner-key-value"
                    ref={keyInput}
                    readOnly
                    value={issued.apiKey}
                    aria-describedby={KEY_DESCRIPTION_ID}
                    autoComplete="off"
                    spellCheck={false}
                    className="min-w-0 font-mono text-xs"
                    onFocus={(event) => event.currentTarget.select()}
                  />
                  <Button type="button" variant="outline" onClick={copy}>
                    <Copy aria-hidden="true" className="size-4" />
                    Copy key
                  </Button>
                </div>
              </div>
            </>
          ) : (
            <>
              <DialogHeader>
                <DialogTitle>Create a partner key</DialogTitle>
                <DialogDescription>
                  A key lets a system of {merchantName} call the partner API as this merchant. Give
                  it a label that says where it will be used.
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
                    ref={labelInput}
                    value={label}
                    maxLength={PARTNER_KEY_LABEL_MAX}
                    autoComplete="off"
                    onChange={(event) => setLabel(event.target.value)}
                    {...aria}
                  />
                )}
              </Field>
            </>
          )}
          {/* Both are always mounted, so a screen reader announces the text when it appears. */}
          <p role="status" className="min-h-5 text-sm text-muted-foreground">
            {announcement}
          </p>
          <p role="alert" className="min-h-5 text-sm font-medium text-destructive">
            {error}
          </p>
          <DialogFooter className="gap-2">
            {issued ? (
              <Button type="button" onClick={() => onOpenChange(false)}>
                Done
              </Button>
            ) : (
              <>
                <Button
                  type="button"
                  variant="outline"
                  aria-disabled={isPending || undefined}
                  className="aria-disabled:cursor-not-allowed aria-disabled:opacity-50"
                  onClick={() => {
                    if (!isPending) onOpenChange(false);
                  }}
                >
                  Cancel
                </Button>
                <Button
                  type="submit"
                  aria-disabled={isPending || undefined}
                  className="aria-disabled:cursor-not-allowed aria-disabled:opacity-50"
                >
                  {isPending ? 'Creating…' : 'Create key'}
                </Button>
              </>
            )}
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
