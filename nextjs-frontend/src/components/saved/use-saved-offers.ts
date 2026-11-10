'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { loadSavedOffersAction } from '@/app/actions/saved-actions';
import type { CardOffer } from '@/lib/catalogue/types';
import { SAVED_OFFERS_REQUEST_LIMIT } from '@/lib/saved/limits';
import { loadSavedChunks, type ChunksOutcome } from '@/lib/saved/load-saved-chunks';
import { removeSavedMany } from '@/lib/saved/saved-store';
import { useHydrated, useSavedSlugs } from '@/lib/saved/use-saved';

export type SavedPhase = 'loading' | 'empty' | 'list';

export interface SavedOffersState {
  phase: SavedPhase;
  /** Offers to show, newest saved first. */
  offers: readonly CardOffer[];
  /** Everything saved on this device, shown or not. */
  total: number;
  /** Slugs in the visible window that the server has not answered for yet. */
  pendingCount: number;
  /** Saved offers the catalogue could not be read for. They stay saved. */
  unavailableCount: number;
  /** Offers dropped this visit because the catalogue said they are gone. */
  removedCount: number;
  /** The whole call failed (or was refused): nothing was removed. */
  failed: boolean;
  /** More saved offers than the window shows, and nothing in the way of asking for them. */
  hasMore: boolean;
  showMore: () => void;
  retry: () => void;
}

interface Resolved {
  loaded: ReadonlyMap<string, CardOffer>;
  unavailable: ReadonlySet<string>;
  /** The catalogue answered nothing at all: every slug not shown counts as unavailable. */
  stopped: boolean;
}

const isUnavailable = (slug: string, { loaded, unavailable, stopped }: Resolved) =>
  unavailable.has(slug) || (stopped && !loaded.has(slug));
const isResolved = (slug: string, resolved: Resolved) =>
  resolved.loaded.has(slug) || isUnavailable(slug, resolved);

/** True for a round where the server read nothing: no offer and nothing it could call gone. */
const readNothing = (outcome: ChunksOutcome) =>
  !outcome.failed &&
  outcome.offers.length === 0 &&
  outcome.missing.length === 0 &&
  outcome.unavailable.length > 0;

/**
 * Turns the browser's saved slugs into cards, one chunk at a time. The slugs come from the store; the cards
 * come from the server action. Only a window of the newest `limit` slugs is asked about (24 to begin with,
 * 24 more each time the visitor presses "Show more"), once per slug. A slug the catalogue says is gone is
 * dropped from the store; anything else that did not load stays saved. After a failed call, or a round that
 * read nothing, nothing is asked again until the visitor presses "Try again".
 */
export function useSavedOffers(): SavedOffersState {
  const hydrated = useHydrated();
  const slugs = useSavedSlugs();
  const [loaded, setLoaded] = useState<ReadonlyMap<string, CardOffer>>(() => new Map());
  const [unavailable, setUnavailable] = useState<ReadonlySet<string>>(() => new Set());
  const [stopped, setStopped] = useState(false);
  const [removedCount, setRemovedCount] = useState(0);
  const [failed, setFailed] = useState(false);
  const [limit, setLimit] = useState(SAVED_OFFERS_REQUEST_LIMIT);
  /** Slugs a call is already out for. A ref: it must not re-run the effect, and must survive Strict Mode. */
  const inFlight = useRef(new Set<string>());
  /** Set while the page is on screen; a late answer for a page that is gone is dropped. */
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const resolved: Resolved = useMemo(
    () => ({ loaded, unavailable, stopped }),
    [loaded, unavailable, stopped]
  );
  const ordered = useMemo(() => [...slugs].reverse(), [slugs]);
  const pending = useMemo(
    () => ordered.slice(0, limit).filter((slug) => !isResolved(slug, resolved)),
    [ordered, limit, resolved]
  );

  useEffect(() => {
    if (!hydrated || failed) return;
    const toAsk = pending.filter((slug) => !inFlight.current.has(slug));
    if (toAsk.length === 0) return;
    toAsk.forEach((slug) => inFlight.current.add(slug));

    void (async () => {
      const outcome = await loadSavedChunks(toAsk, loadSavedOffersAction, {
        shouldContinue: () => mounted.current,
      });
      toAsk.forEach((slug) => inFlight.current.delete(slug));
      if (!mounted.current) return;
      setLoaded(
        (previous) => new Map([...previous, ...outcome.offers.map((o) => [o.slug, o] as const)])
      );
      if (outcome.unavailable.length > 0) {
        setUnavailable((previous) => new Set([...previous, ...outcome.unavailable]));
      }
      if (readNothing(outcome)) setStopped(true);
      // Only what was really removed is announced: the visitor may have removed a slug meanwhile.
      const removed = removeSavedMany(outcome.missing);
      if (removed > 0) setRemovedCount((count) => count + removed);
      if (outcome.failed) setFailed(true);
    })();
  }, [hydrated, failed, pending]);

  const showMore = useCallback(
    () => setLimit((current) => current + SAVED_OFFERS_REQUEST_LIMIT),
    []
  );
  const retry = useCallback(() => {
    setUnavailable(new Set());
    setStopped(false);
    setFailed(false);
  }, []);

  const offers = useMemo(
    () => ordered.flatMap((slug) => loaded.get(slug) ?? []),
    [ordered, loaded]
  );
  const pendingCount = failed ? 0 : pending.length;

  let phase: SavedPhase = 'list';
  if (!hydrated) phase = 'loading';
  else if (slugs.length === 0) phase = 'empty';
  else if (offers.length === 0 && pendingCount > 0) phase = 'loading';

  return {
    phase,
    offers,
    total: slugs.length,
    pendingCount,
    unavailableCount: slugs.filter((slug) => isUnavailable(slug, resolved)).length,
    removedCount,
    failed,
    hasMore: !failed && ordered.slice(limit).some((slug) => !isResolved(slug, resolved)),
    showMore,
    retry,
  };
}
