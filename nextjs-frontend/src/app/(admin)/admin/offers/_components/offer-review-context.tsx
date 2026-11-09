'use client';

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';

/**
 * What the edit page knows about the review token of the offer it shows.
 *
 * The page loads an offer with a `review_token`; every write the editor makes (a save, a status step) returns the
 * token of the version it stored. When the page then reloads with a different token, or the API refuses a step
 * with `offer_changed`, somebody else changed the offer: `changed` turns on and stays on (a toast is gone after a
 * few seconds) until the editor's next successful write.
 *
 * A reload can land before the write's own result does, so while a write is in flight the loaded token is only
 * noted; the comparison happens when the result arrives.
 */
type OfferReview = {
  changed: boolean;
  beginWrite: () => void;
  /** A write succeeded; `token` is the review token of the version it stored. */
  recordWrite: (token: string | undefined) => void;
  /** A write was refused; `offer_changed` means the offer moved since it was loaded. */
  recordFailure: (reason?: 'offer_changed') => void;
  /** The token the page was last loaded with. */
  watchLoadedToken: (token: string) => void;
};

const NOOP: OfferReview = {
  changed: false,
  beginWrite: () => undefined,
  recordWrite: () => undefined,
  recordFailure: () => undefined,
  watchLoadedToken: () => undefined,
};

const OfferReviewContext = createContext<OfferReview>(NOOP);

export function OfferReviewProvider({ children }: { children: ReactNode }) {
  const [changed, setChanged] = useState(false);
  const own = useRef<string | null>(null);
  const loaded = useRef<string | null>(null);
  const loadedAtStart = useRef<string | null>(null);
  const inFlight = useRef(false);

  const value = useMemo<OfferReview>(
    () => ({
      changed,
      beginWrite() {
        inFlight.current = true;
        loadedAtStart.current = loaded.current;
      },
      recordWrite(token) {
        inFlight.current = false;
        setChanged(false);
        if (token === undefined) return;
        own.current = token;
        const now = loaded.current;
        // A different version landed while the write ran: not the editor's own.
        if (now !== null && now !== loadedAtStart.current && now !== token) setChanged(true);
      },
      recordFailure(reason) {
        inFlight.current = false;
        if (reason === 'offer_changed') setChanged(true);
      },
      watchLoadedToken(token) {
        loaded.current = token;
        if (!inFlight.current && own.current !== null && token !== own.current) setChanged(true);
      },
    }),
    [changed]
  );

  return <OfferReviewContext.Provider value={value}>{children}</OfferReviewContext.Provider>;
}

export function useOfferReview(): OfferReview {
  return useContext(OfferReviewContext);
}

/** Tells the review state which token the page now shows (runs after every reload). */
export function useReportLoadedToken(token: string): void {
  const { watchLoadedToken } = useOfferReview();
  useEffect(() => watchLoadedToken(token), [watchLoadedToken, token]);
}
