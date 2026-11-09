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
 * `seen` is the last version of the offer the editor has actually been shown: it starts at the token the page
 * first loaded with. The editor's own write (a save, a status step) returns the token of the state after it AND
 * `previous`, the token of the state it replaced under the row lock. If `previous` is not `seen`, somebody else
 * changed the offer before the write and the page is about to show their version: `changed` turns on and stays on
 * (a toast is gone after a few seconds) until the editor's next successful write. A reload that brings a token
 * other than `seen` (a refused step, a link edited elsewhere) does the same. `seen` then moves to that token.
 *
 * A reload can land before the write's own result does, so while a write is in flight the loaded token is only
 * noted; when the result arrives a version other than the editor's own that landed meanwhile still raises it.
 *
 * The handlers only touch refs and `setChanged`, so they are created once: a changing identity would re-run the
 * page's "this token was loaded" effect with its still-old token and raise the notice again after a success.
 */
type OfferReviewHandlers = {
  beginWrite: () => void;
  /** A write succeeded: the token after it and the token of the state it replaced (when the API said). */
  recordWrite: (token: string | undefined, previous?: string) => void;
  /** A write was refused; `offer_changed` means the offer moved since it was loaded. */
  recordFailure: (reason?: 'offer_changed') => void;
  /** The token the page was just loaded with. */
  watchLoadedToken: (token: string) => void;
};
type OfferReview = OfferReviewHandlers & { changed: boolean };

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
  const seen = useRef<string | null>(null);
  const loaded = useRef<string | null>(null);
  const loadedAtStart = useRef<string | null>(null);
  const inFlight = useRef(false);

  const handlers = useMemo<OfferReviewHandlers>(
    () => ({
      beginWrite() {
        inFlight.current = true;
        loadedAtStart.current = loaded.current;
      },
      recordWrite(token, previous) {
        inFlight.current = false;
        const foreignBefore = previous !== undefined && previous !== seen.current;
        const now = loaded.current;
        // A different version landed while the write ran: not the editor's own.
        const foreignMeanwhile =
          token !== undefined && now !== null && now !== loadedAtStart.current && now !== token;
        setChanged(foreignBefore || foreignMeanwhile);
        if (token !== undefined) seen.current = token;
      },
      recordFailure(reason) {
        inFlight.current = false;
        if (reason === 'offer_changed') setChanged(true);
      },
      watchLoadedToken(token) {
        loaded.current = token;
        if (seen.current === null) {
          seen.current = token;
          return;
        }
        if (inFlight.current) return;
        if (token !== seen.current) {
          setChanged(true);
          seen.current = token;
        }
      },
    }),
    []
  );
  const value = useMemo<OfferReview>(() => ({ ...handlers, changed }), [handlers, changed]);

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
