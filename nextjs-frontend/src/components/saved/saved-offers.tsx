'use client';

import { useEffect, useRef } from 'react';
import Link from 'next/link';
import { TriangleAlert } from 'lucide-react';
import { OfferGrid } from '@/components/catalogue/offer-card';
import { Spark } from '@/components/site/brand-mark';
import { useSavedOffers, type SavedOffersState } from './use-saved-offers';

const BUTTON_CLASS =
  'inline-flex min-h-11 items-center rounded-pill border border-border bg-background px-5 text-sm font-bold text-foreground transition-colors hover:bg-muted';

function plural(count: number, one: string, many: string): string {
  return count === 1 ? one : many;
}

function SavedSkeleton() {
  return (
    <div role="status" aria-busy="true" aria-label="Loading your saved freebies">
      <ul
        aria-hidden="true"
        className="grid grid-cols-2 gap-2.5 sm:gap-5 md:grid-cols-3 xl:grid-cols-4"
      >
        {[0, 1, 2, 3].map((key) => (
          <li key={key} className="h-72 animate-pulse rounded-card border border-border bg-muted" />
        ))}
      </ul>
    </div>
  );
}

function SavedEmptyState() {
  return (
    <section
      aria-labelledby="saved-empty-heading"
      className="rounded-editorial border border-border bg-card px-6 py-12 text-center shadow-sm"
    >
      <span className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-secondary text-cta">
        <Spark className="h-7 w-7" />
      </span>
      <h2
        id="saved-empty-heading"
        className="mt-5 font-display text-2xl font-extrabold text-foreground"
      >
        Nothing saved yet
      </h2>
      <p className="mx-auto mt-2 max-w-md text-sm text-muted-foreground">
        Tap the heart on any freebie to keep it here. It stays on this device, so you can come back
        to it later.
      </p>
      <Link
        href="/freebies"
        className="mt-6 inline-flex min-h-11 items-center rounded-pill bg-cta px-6 text-sm font-bold text-cta-foreground transition-colors hover:bg-cta-hover"
      >
        Browse freebies
      </Link>
    </section>
  );
}

function LoadFailed({ onRetry }: { onRetry: () => void }) {
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center justify-between gap-4 rounded-card border border-destructive/50 bg-card p-5 text-foreground"
    >
      <div className="flex min-w-0 items-start gap-3">
        <TriangleAlert aria-hidden="true" className="mt-0.5 h-5 w-5 shrink-0 text-destructive" />
        <div>
          <p className="font-display text-base font-bold">Your saved freebies are not loading</p>
          <p className="mt-1 text-sm text-muted-foreground">
            We could not load your saved freebies just now. They are still saved on this device.
          </p>
        </div>
      </div>
      <button type="button" onClick={onRetry} className={BUTTON_CLASS}>
        Try again
      </button>
    </div>
  );
}

function SomeUnavailable({ count, onRetry }: { count: number; onRetry: () => void }) {
  return (
    <div className="mb-6 flex flex-wrap items-center justify-between gap-3 rounded-card border border-border bg-card p-4 text-sm">
      <p className="text-muted-foreground">
        {count} saved {plural(count, 'freebie', 'freebies')} could not be loaded just now.{' '}
        {plural(count, 'It is', 'They are')} still saved.
      </p>
      <button type="button" onClick={onRetry} className={BUTTON_CLASS}>
        Try again
      </button>
    </div>
  );
}

/** Heading text for the live region: the count once it is known, then any "removed" notice. */
function summaryText(state: SavedOffersState): string {
  const parts: string[] = [];
  if (state.phase === 'list' && state.pendingCount === 0 && !state.failed) {
    parts.push(
      `${state.offers.length} saved ${plural(state.offers.length, 'freebie', 'freebies')}`
    );
  }
  if (state.removedCount > 0) {
    const n = state.removedCount;
    parts.push(
      `${n} saved ${plural(n, 'freebie is', 'freebies are')} no longer available and ${plural(n, 'was', 'were')} removed from your list.`
    );
  }
  return parts.join(' ');
}

/** After the focused card disappears (its heart was pressed), keep keyboard users on the page. */
function useRestoreFocus(count: number, target: React.RefObject<HTMLElement | null>) {
  const previous = useRef(count);
  useEffect(() => {
    const dropped = count < previous.current;
    previous.current = count;
    const active = document.activeElement;
    if (dropped && (active === null || active === document.body)) target.current?.focus();
  }, [count, target]);
}

/**
 * The Saved page's list (client island, FREEJ-28). It reads the saved slugs from this device, asks the
 * server which are still public offers, and renders them with the same `OfferGrid` the catalogue uses.
 * Until the browser's list is read it shows a skeleton (never "nothing saved"), so a saved list does not
 * flash empty.
 */
export function SavedOffers() {
  const state = useSavedOffers();
  const summaryRef = useRef<HTMLDivElement>(null);
  useRestoreFocus(state.offers.length, summaryRef);

  const text = summaryText(state);
  let body: React.ReactNode;
  if (state.phase === 'loading') body = <SavedSkeleton />;
  else if (state.phase === 'empty') body = <SavedEmptyState />;
  else if (state.failed && state.offers.length === 0) body = <LoadFailed onRetry={state.retry} />;
  else {
    body = (
      <>
        {state.failed && <LoadFailed onRetry={state.retry} />}
        {state.unavailableCount > 0 && (
          <SomeUnavailable count={state.unavailableCount} onRetry={state.retry} />
        )}
        <OfferGrid offers={[...state.offers]} merchants={[]} />
      </>
    );
  }

  return (
    <div>
      <div
        ref={summaryRef}
        tabIndex={-1}
        aria-live="polite"
        className={text === '' ? 'sr-only' : 'mb-6 text-sm font-semibold text-muted-foreground'}
      >
        {text}
      </div>
      {body}
    </div>
  );
}
