'use client';

import { useLayoutEffect, useRef } from 'react';
import Link from 'next/link';
import { TriangleAlert } from 'lucide-react';
import { OfferGrid } from '@/components/catalogue/offer-card';
import { Spark } from '@/components/site/brand-mark';
import { useSavedOffers, type SavedOffersState } from './use-saved-offers';

const BUTTON_CLASS =
  'inline-flex min-h-11 items-center rounded-pill border border-border bg-background px-5 text-sm font-bold text-foreground transition-colors hover:bg-muted';
const EMPTY_HEADING_ID = 'saved-empty-heading';
const RETRY_BUTTON_ID = 'saved-retry';
const HEART_SELECTOR = 'button[aria-pressed]';

function plural(count: number, one: string, many: string): string {
  return count === 1 ? one : many;
}

function SavedSkeleton() {
  return (
    <div>
      <p className="sr-only">Loading your saved freebies</p>
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
      aria-labelledby={EMPTY_HEADING_ID}
      className="rounded-editorial border border-border bg-card px-6 py-12 text-center shadow-sm"
    >
      <span className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-secondary text-cta">
        <Spark className="h-7 w-7" />
      </span>
      {/* tabIndex -1: focus lands here when the last saved card is removed. */}
      <h2
        id={EMPTY_HEADING_ID}
        tabIndex={-1}
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
      className="mb-6 flex flex-wrap items-center justify-between gap-4 rounded-card border border-destructive/50 bg-card p-5 text-foreground"
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
      <button type="button" id={RETRY_BUTTON_ID} onClick={onRetry} className={BUTTON_CLASS}>
        Try again
      </button>
    </div>
  );
}

function SomeUnavailable({ count, onRetry }: { count: number; onRetry: () => void }) {
  return (
    <div
      role="status"
      className="mb-6 flex flex-wrap items-center justify-between gap-3 rounded-card border border-border bg-card p-4 text-sm"
    >
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

/** Text for the polite live region: progress, then the count (honest about what is not shown), then any removal. */
function summaryText(state: SavedOffersState): string {
  const parts: string[] = [];
  if (state.pendingCount > 0) {
    parts.push('Loading your saved freebies');
  } else if (state.phase === 'list' && !state.failed && state.offers.length > 0) {
    const shown = state.offers.length;
    parts.push(
      state.unavailableCount === 0 && !state.hasMore
        ? `${shown} saved ${plural(shown, 'freebie', 'freebies')}`
        : `${shown} of ${state.total} saved freebies shown`
    );
  }
  if (state.removedCount > 0) {
    const n = state.removedCount;
    parts.push(
      `${n} saved ${plural(n, 'freebie is', 'freebies are')} no longer available and ${plural(n, 'was', 'were')} removed from your list.`
    );
  }
  return parts.join('. ');
}

/** The heart that should take focus after a card left the list: the next one, or the previous if it was last. */
function focusHeartAfterRemoval(root: HTMLElement | null, before: string[], now: string[]) {
  if (now.length === 0) {
    document.getElementById(EMPTY_HEADING_ID)?.focus({ preventScroll: true });
    return;
  }
  const removedAt = before.findIndex((slug) => !now.includes(slug));
  const target = Math.min(Math.max(removedAt, 0), now.length - 1);
  root?.querySelectorAll<HTMLElement>(HEART_SELECTOR)[target]?.focus({ preventScroll: true });
}

/**
 * Keeps keyboard focus on the page when the list changes under it:
 * - a heart pressed in this list removed a card: focus the next card's heart (or the empty heading);
 * - "Show more" loaded cards: focus the first new card's heart, or "Try again" if that failed;
 * - a change that did not start here (another tab, a prune) never moves focus.
 */
function useListFocus(state: SavedOffersState, root: React.RefObject<HTMLElement | null>) {
  const removalHere = useRef(false);
  const previous = useRef<string[]>([]);
  const afterMore = useRef<number | null>(null);

  useLayoutEffect(() => {
    const now = state.offers.map((offer) => offer.slug);
    const before = previous.current;
    previous.current = now;

    if (removalHere.current && now.length < before.length) {
      removalHere.current = false;
      focusHeartAfterRemoval(root.current, before, now);
    }
    if (afterMore.current === null) return;
    if (state.failed) {
      afterMore.current = null;
      document.getElementById(RETRY_BUTTON_ID)?.focus();
    } else if (now.length > afterMore.current) {
      const first = root.current?.querySelectorAll<HTMLElement>(HEART_SELECTOR)[afterMore.current];
      afterMore.current = null;
      first?.focus();
    }
  }, [state.offers, state.failed, root]);

  const markRemoval = (event: React.MouseEvent) => {
    if (!(event.target as Element).closest(HEART_SELECTOR)) return;
    removalHere.current = true;
    // The store updates inside this click; if nothing was removed by then, forget it.
    setTimeout(() => {
      removalHere.current = false;
    }, 0);
  };
  const markShowMore = () => {
    afterMore.current = state.offers.length;
    state.showMore();
  };
  return { markRemoval, markShowMore };
}

function ShowMoreButton({ loading, onClick }: { loading: boolean; onClick: () => void }) {
  return (
    <div className="mt-8 flex justify-center">
      <button
        type="button"
        aria-disabled={loading}
        onClick={loading ? undefined : onClick}
        className={BUTTON_CLASS}
      >
        {loading ? 'Loading…' : 'Show more'}
      </button>
    </div>
  );
}

/**
 * The Saved page's list (client island, FREEJ-28). It reads the saved slugs from this device, asks the
 * server which are still public offers (24 at a time), and renders them with the same `OfferGrid` the
 * catalogue uses. Until the browser's list is read it shows a skeleton (never "nothing saved"), so a saved
 * list does not flash empty.
 */
export function SavedOffers() {
  const state = useSavedOffers();
  const summaryRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const { markRemoval, markShowMore } = useListFocus(state, listRef);

  const retry = () => {
    state.retry();
    summaryRef.current?.focus();
  };
  const noneShown = state.offers.length === 0;
  const loadingMore = state.pendingCount > 0 && !noneShown;

  let body: React.ReactNode;
  if (state.phase === 'empty') body = <SavedEmptyState />;
  else if (
    state.phase === 'loading' ||
    (noneShown && !state.failed && state.unavailableCount === 0)
  ) {
    body = <SavedSkeleton />;
  } else if (noneShown) body = <LoadFailed onRetry={retry} />;
  else {
    body = (
      <>
        {state.failed && <LoadFailed onRetry={retry} />}
        {state.unavailableCount > 0 && (
          <SomeUnavailable count={state.unavailableCount} onRetry={retry} />
        )}
        <div ref={listRef} onClickCapture={markRemoval}>
          <h2 className="sr-only">Your saved freebies</h2>
          <OfferGrid offers={[...state.offers]} merchants={[]} />
        </div>
        {(state.hasMore || loadingMore) && (
          <ShowMoreButton loading={loadingMore} onClick={markShowMore} />
        )}
      </>
    );
  }

  const text = summaryText(state);
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
