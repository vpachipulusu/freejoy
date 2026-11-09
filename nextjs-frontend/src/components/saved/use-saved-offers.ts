'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { loadSavedOffersAction } from '@/app/actions/saved-actions';
import type { Offer } from '@/lib/catalogue/types';
import { SAVED_OFFERS_REQUEST_LIMIT } from '@/lib/saved/limits';
import { removeSavedMany } from '@/lib/saved/saved-store';
import { useHydrated, useSavedSlugs } from '@/lib/saved/use-saved';

export type SavedPhase = 'loading' | 'empty' | 'list';

export interface SavedOffersState {
  phase: SavedPhase;
  /** Offers to show, newest saved first. */
  offers: readonly Offer[];
  /** Saved slugs the server has not answered for yet. */
  pendingCount: number;
  /** Saved offers the catalogue could not be read for. They stay saved. */
  unavailableCount: number;
  /** Offers dropped this visit because they are no longer public. */
  removedCount: number;
  /** The whole call failed (or was refused): nothing was removed. */
  failed: boolean;
  retry: () => void;
}

function chunked<T>(items: readonly T[], size: number): T[][] {
  const chunks: T[][] = [];
  for (let start = 0; start < items.length; start += size)
    chunks.push(items.slice(start, start + size));
  return chunks;
}

async function askServer(chunk: string[]) {
  try {
    return await loadSavedOffersAction(chunk);
  } catch {
    // A network error or a refused call: the same curated failure, no detail kept.
    return { success: false as const, error: '' };
  }
}

/**
 * Turns the browser's saved slugs into offers. The slugs come from the store; the offers come from the
 * server action, once per slug (a slug saved later, say in another tab, is the only one asked for next).
 * A slug the catalogue says is gone is dropped from the store; a slug it could not read stays. After a
 * failed call nothing is retried until the visitor asks.
 */
export function useSavedOffers(): SavedOffersState {
  const hydrated = useHydrated();
  const slugs = useSavedSlugs();
  const [loaded, setLoaded] = useState<Readonly<Record<string, Offer>>>({});
  const [unavailable, setUnavailable] = useState<readonly string[]>([]);
  const [removedCount, setRemovedCount] = useState(0);
  const [failed, setFailed] = useState(false);
  /** Slugs a call is already out for. A ref: it must not re-run the effect, and must survive Strict Mode. */
  const inFlight = useRef(new Set<string>());

  const pending = useMemo(
    () => slugs.filter((slug) => !(slug in loaded) && !unavailable.includes(slug)),
    [slugs, loaded, unavailable]
  );

  useEffect(() => {
    if (!hydrated || failed) return;
    const toAsk = pending.filter((slug) => !inFlight.current.has(slug));
    if (toAsk.length === 0) return;
    toAsk.forEach((slug) => inFlight.current.add(slug));

    void (async () => {
      for (const chunk of chunked(toAsk, SAVED_OFFERS_REQUEST_LIMIT)) {
        const result = await askServer(chunk);
        if (!result.success) {
          toAsk.forEach((slug) => inFlight.current.delete(slug));
          setFailed(true);
          return;
        }
        const { offers, missing, unavailable: unreadable } = result.data;
        chunk.forEach((slug) => inFlight.current.delete(slug));
        setLoaded((previous) => ({
          ...previous,
          ...Object.fromEntries(offers.map((offer) => [offer.slug, offer])),
        }));
        if (unreadable.length > 0) setUnavailable((previous) => [...previous, ...unreadable]);
        if (missing.length > 0) {
          removeSavedMany(missing);
          setRemovedCount((count) => count + missing.length);
        }
      }
    })();
  }, [hydrated, failed, pending]);

  const retry = useCallback(() => {
    setUnavailable([]);
    setFailed(false);
  }, []);

  const offers = useMemo(
    () => [...slugs].reverse().flatMap((slug) => loaded[slug] ?? []),
    [slugs, loaded]
  );
  const unresolved = failed ? 0 : pending.length;
  const unavailableNow = slugs.filter((slug) => unavailable.includes(slug)).length;

  let phase: SavedPhase = 'list';
  if (!hydrated) phase = 'loading';
  else if (slugs.length === 0) phase = 'empty';
  else if (offers.length === 0 && unresolved > 0) phase = 'loading';

  return {
    phase,
    offers,
    pendingCount: unresolved,
    unavailableCount: unavailableNow,
    removedCount,
    failed,
    retry,
  };
}
