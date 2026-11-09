import type { Metadata } from 'next';
import { Smartphone } from 'lucide-react';
import { SavedOffers } from '@/components/saved/saved-offers';
import { NOINDEX_FOLLOW_ROBOTS } from '@/lib/seo/metadata';

/**
 * The Saved list (FREEJ-28). The list lives in the visitor's browser, so this page is the same shell for
 * everybody: it reads no cookie and no header, calls no backend while rendering, and the island below
 * does the rest. It is noindex (it is different on every device) and not in the sitemap.
 */
export const metadata: Metadata = {
  title: 'Saved freebies',
  description: 'The freebies you saved on this device.',
  robots: NOINDEX_FOLLOW_ROBOTS,
};

export default function SavedPage() {
  return (
    <div className="mx-auto max-w-content px-4 py-8 sm:px-6 sm:py-12">
      <h1 className="font-display text-3xl font-extrabold tracking-tight text-foreground sm:text-4xl">
        Saved freebies
      </h1>
      <aside
        role="note"
        className="mt-4 flex max-w-2xl items-start gap-3 rounded-card border border-border bg-secondary p-4 text-sm text-foreground"
      >
        <Smartphone aria-hidden="true" className="mt-0.5 h-5 w-5 shrink-0 text-accent-ink" />
        <div>
          <p className="font-bold">Saved on this device</p>
          <p className="mt-1 text-muted-foreground">
            Your saved freebies are kept in this browser only. They are not synced to other devices
            or to an account, and clearing your browser data removes them.
          </p>
        </div>
      </aside>
      <div className="mt-8">
        <SavedOffers />
        <noscript>
          <p className="rounded-card border border-border bg-card p-5 text-sm text-muted-foreground">
            Saved freebies need JavaScript, because the list is kept in your browser. You can still
            browse every freebie on the freebies page.
          </p>
        </noscript>
      </div>
    </div>
  );
}
