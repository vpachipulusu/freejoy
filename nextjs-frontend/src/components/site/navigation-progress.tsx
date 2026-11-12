'use client';

import { Suspense, useEffect, useRef, useState } from 'react';
import { usePathname, useSearchParams } from 'next/navigation';

import { Spinner } from '@/components/ui/spinner';
import {
  NAVIGATION_START_EVENT,
  isAnnouncedNavigation,
  resolveClickDestination,
  resolveFormDestination,
  routeKeyOf,
} from './navigation-events';

/**
 * The indicator never stays longer than this, whatever happens to the navigation. Outcomes that end on the
 * same URL (a redirect back to the page, the admin bounce) clear only here, so it is kept short.
 */
export const NAVIGATION_TIMEOUT_MS = 5_000;

/** A navigation that finishes within this time (a prefetched page) never shows the indicator. */
export const NAVIGATION_SHOW_DELAY_MS = 100;

type Navigation = {
  /** Counts the navigations started since the last stop, so a second click restarts the timers. */
  id: number;
  /** True once the show delay has passed. */
  shown: boolean;
};

/**
 * Global "a page is loading" indicator, mounted once in the root layout: a thin bar across the top of the
 * window plus a small spinner. It covers what a route `loading.tsx` cannot: moving between pages under one
 * shared layout (admin Freebies to Users), GET search forms, and routes without a loading boundary.
 *
 * Start: a click on a same-origin link to another page, a native GET form submit, back or forward to another
 * page, or `announceNavigation()` (for `router.push` and `next/form`, which cancel the native event). The
 * bar appears after `NAVIGATION_SHOW_DELAY_MS`. Stop: the path or query string changes (the new page has
 * rendered), `NAVIGATION_TIMEOUT_MS`, or a page restored from the back-forward cache. It is never shown on
 * the first page load, ignores hash-only changes, and is `pointer-events-none` and fixed, so it neither
 * blocks clicks nor shifts the layout. One persistent `role="status"` region announces "Loading" to
 * screen readers.
 */
export function NavigationProgress() {
  // useSearchParams needs a Suspense boundary above it, or static pages would opt out of prerendering.
  return (
    <Suspense fallback={null}>
      <NavigationProgressInner />
    </Suspense>
  );
}

function NavigationProgressInner() {
  const pathname = usePathname();
  const search = useSearchParams().toString();
  const routeKey = routeKeyOf(pathname, search);

  const [navigation, setNavigation] = useState<Navigation | null>(null);
  const [shownRouteKey, setShownRouteKey] = useState(routeKey);
  const routeKeyRef = useRef(routeKey);

  // A new route has rendered: stop. (Adjusting state during render, not in an effect, so there is no extra paint.)
  if (shownRouteKey !== routeKey) {
    setShownRouteKey(routeKey);
    setNavigation(null);
  }

  useEffect(() => {
    routeKeyRef.current = routeKey;
  }, [routeKey]);

  useEffect(() => {
    const submitTimers = new Set<number>();
    const start = () =>
      setNavigation((current) => ({ id: (current?.id ?? 0) + 1, shown: current?.shown ?? false }));
    const stop = () => setNavigation(null);

    const onClick = (event: MouseEvent) => {
      if (resolveClickDestination(event, window.location)) start();
    };

    const onSubmit = (event: Event) => {
      // A form that script handles calls preventDefault; only a native submit navigates to the new URL.
      // Decided after the event, and only then is the destination worked out (it reads the form fields).
      const timer = window.setTimeout(() => {
        submitTimers.delete(timer);
        if (!event.defaultPrevented && resolveFormDestination(event, window.location)) start();
      }, 0);
      submitTimers.add(timer);
    };

    const onPopState = () => {
      const { pathname: path, search: query } = window.location;
      if (routeKeyOf(path, query) !== routeKeyRef.current) start();
    };

    const onAnnounced = (event: Event) => {
      if (isAnnouncedNavigation(event, window.location)) start();
    };

    const onPageShow = (event: PageTransitionEvent) => {
      if (event.persisted) stop();
    };

    document.addEventListener('click', onClick, true);
    document.addEventListener('submit', onSubmit, true);
    window.addEventListener('popstate', onPopState);
    window.addEventListener(NAVIGATION_START_EVENT, onAnnounced);
    window.addEventListener('pageshow', onPageShow);
    return () => {
      document.removeEventListener('click', onClick, true);
      document.removeEventListener('submit', onSubmit, true);
      window.removeEventListener('popstate', onPopState);
      window.removeEventListener(NAVIGATION_START_EVENT, onAnnounced);
      window.removeEventListener('pageshow', onPageShow);
      submitTimers.forEach((timer) => window.clearTimeout(timer));
    };
  }, []);

  const navigationId = navigation?.id ?? null;
  useEffect(() => {
    if (navigationId === null) return;
    const showTimer = window.setTimeout(() => {
      setNavigation((current) =>
        current?.id === navigationId ? { ...current, shown: true } : current
      );
    }, NAVIGATION_SHOW_DELAY_MS);
    const stopTimer = window.setTimeout(() => setNavigation(null), NAVIGATION_TIMEOUT_MS);
    return () => {
      window.clearTimeout(showTimer);
      window.clearTimeout(stopTimer);
    };
  }, [navigationId]);

  const shown = navigation?.shown === true;

  return (
    <>
      <span role="status" data-testid="navigation-progress-status" className="sr-only">
        {shown ? 'Loading' : ''}
      </span>
      {shown && (
        <>
          <div
            aria-hidden="true"
            data-testid="navigation-progress"
            className="nav-progress-fade pointer-events-none fixed inset-x-0 top-0 z-[100] h-1 overflow-hidden"
          >
            <div className="nav-progress-bar h-full w-full bg-accent-ink" />
          </div>
          <div
            data-testid="navigation-progress-spinner"
            className="nav-progress-fade pointer-events-none fixed bottom-[calc(4.5rem+env(safe-area-inset-bottom))] start-1/2 z-40 -translate-x-1/2 rounded-full border border-border bg-background p-2 shadow-lg lg:bottom-6"
          >
            <Spinner decorative size="md" />
          </div>
        </>
      )}
    </>
  );
}
