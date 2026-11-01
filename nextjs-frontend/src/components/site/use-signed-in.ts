'use client';

import { useEffect, useState } from 'react';

export const SESSION_STATUS_URL = '/api/auth/session-status';

/**
 * Asks the cosmetic session-status route whether a `session` cookie exists. The public pages are static and
 * session-blind, so this runs once after hydration, in the browser. It starts `false` and stays `false` on a
 * non-200 answer, a malformed body or a network error: the header then keeps today's "Log in". There is no
 * cache, so a visitor who just signed in or out sees the right link as soon as the public layout mounts again.
 * The request is aborted on unmount.
 */
export function useSignedIn(): boolean {
  const [signedIn, setSignedIn] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    fetch(SESSION_STATUS_URL, {
      cache: 'no-store',
      credentials: 'same-origin',
      signal: controller.signal,
    })
      .then((response) => (response.ok ? response.json() : null))
      .then((body: unknown) => {
        if (controller.signal.aborted) return;
        const status = body as { signedIn?: unknown } | null;
        setSignedIn(status?.signedIn === true);
      })
      .catch(() => {
        // Aborted or unreachable: keep the signed-out default.
      });
    return () => controller.abort();
  }, []);

  return signedIn;
}
