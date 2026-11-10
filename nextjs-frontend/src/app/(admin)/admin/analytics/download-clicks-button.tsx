'use client';

import { useRef, useState } from 'react';
import { Download } from 'lucide-react';
import { exportMerchantClicksAction } from '@/app/actions/admin/merchant-clicks-actions';
import { Button } from '@/components/ui/button';
import { saveCsvFile } from '@/lib/admin-clicks/download';

export const DOWNLOAD_FAILED_MESSAGE = 'The file could not be created. Please try again.';
export const TRUNCATED_FILE_NOTE = 'The file lists only the offers shown in the report.';

type Props = { merchantId: string; from: string; to: string };

type Status =
  | { kind: 'idle' }
  | { kind: 'busy' }
  | { kind: 'done'; message: string }
  | { kind: 'error'; message: string };

/**
 * "Download CSV": asks the server action for the file (admin gate and validation run there), then saves it. The
 * progress and the result are announced in a polite status region and a failure in an alert, both always in the
 * page so a screen reader hears the change. The button is a real button: Enter and Space work. While it works it
 * is `aria-disabled` (dimmed by class) rather than `disabled`, so keyboard focus stays on it.
 */
export function DownloadClicksButton({ merchantId, from, to }: Props) {
  const [status, setStatus] = useState<Status>({ kind: 'idle' });
  const busy = status.kind === 'busy';
  // A ref, not the state: a second press in the same tick must not start a second download.
  const working = useRef(false);

  async function download() {
    if (working.current) return;
    working.current = true;
    setStatus({ kind: 'busy' });
    try {
      const result = await exportMerchantClicksAction({ merchantId, from, to });
      if (!result.success) {
        setStatus({ kind: 'error', message: result.error });
        return;
      }
      saveCsvFile(result.data.filename, result.data.csv);
      const note = result.data.truncated ? ` ${TRUNCATED_FILE_NOTE}` : '';
      setStatus({ kind: 'done', message: `Download started: ${result.data.filename}.${note}` });
    } catch {
      setStatus({ kind: 'error', message: DOWNLOAD_FAILED_MESSAGE });
    } finally {
      working.current = false;
    }
  }

  return (
    <div className="space-y-2">
      <Button
        type="button"
        variant="outline"
        onClick={download}
        aria-disabled={busy || undefined}
        className={busy ? 'opacity-50' : undefined}
      >
        <Download aria-hidden="true" />
        Download CSV
      </Button>
      <p role="status" className="text-sm text-muted-foreground">
        {busy ? 'Preparing the file…' : status.kind === 'done' ? status.message : ''}
      </p>
      <p role="alert" className="text-sm text-destructive">
        {status.kind === 'error' ? status.message : ''}
      </p>
    </div>
  );
}
