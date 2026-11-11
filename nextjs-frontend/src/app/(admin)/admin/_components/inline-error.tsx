import type { ReactNode } from 'react';
import Link from 'next/link';
import { TriangleAlert } from 'lucide-react';
import { Button } from '@/components/ui/button';

type InlineErrorProps = {
  /** Already curated by `adminErrorMessage`; never backend text. */
  message: string;
  /** The page's own path: following it re-runs the server fetch. */
  retryHref?: string;
  /** Replaces the Retry link, for a retry that needs to show progress and keep focus. */
  retry?: ReactNode;
};

/** Inline error for admin pages: the curated message plus a Retry link that reloads the page. */
export function InlineError({ message, retryHref, retry }: InlineErrorProps) {
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-destructive/50 p-4 text-sm text-destructive"
    >
      <p className="flex items-center gap-2">
        <TriangleAlert aria-hidden="true" className="size-4 shrink-0" />
        {message}
      </p>
      {retry ?? (
        <Button variant="outline" asChild>
          <Link href={retryHref ?? '#'}>Retry</Link>
        </Button>
      )}
    </div>
  );
}
