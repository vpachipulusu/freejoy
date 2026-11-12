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

/** The indicator never stays longer than this, whatever happens to the navigation (error page, same URL). */
export const NAVIGATION_TIMEOUT_MS = 10_000;

/**
 * Global "a page is loading" indicator, mounted once in the root layout: a thin bar across the top of the
 * window plus a small spinner. It covers what a route `loading.tsx` cannot: moving between pages under one
 * shared layout (admin Freebies to Users), GET search forms, and routes without a loading boundary.
 *
 * Start: a click on a same-origin link to another page, a GET form submit that is not handled by script, back
 * or forward to another page, or `announceNavigation()` (for `router.push`). Stop: the path or query string
 * changes (the new page has rendered), the safety timeout, a page error, or a page restored from the
 * back-forward cache. It is never shown on the first page load, ignores hash-only changes and is
 * `pointer-events-none` and fixed, so it neither blocks clicks nor shifts the layout.
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
  const routeKey = routeKeyOf(pathname, search ? `?${search}` : '');

  // The number of navigations started since the last stop; null when nothing is loading.
  const [started, setStarted] = useState<number | null>(null);
  const [shownRouteKey, setShownRouteKey] = useState(routeKey);
  const routeKeyRef = useRef(routeKey);

  // A new route has rendered: stop. (Adjusting state during render, not in an effect, so there is no extra paint.)
  if (shownRouteKey !== routeKey) {
    setShownRouteKey(routeKey);
    setStarted(null);
  }

  useEffect(() => {
    routeKeyRef.current = routeKey;
  }, [routeKey]);

  useEffect(() => {
    const start = () => setStarted((count) => (count ?? 0) + 1);
    const stop = () => setStarted(null);

    const onClick = (event: MouseEvent) => {
      if (resolveClickDestination(event, window.location)) start();
    };

    const onSubmit = (event: Event) => {
      if (!resolveFormDestination(event, window.location)) return;
      // A form that script handles calls preventDefault; only a native submit navigates to the new URL.
      window.setTimeout(() => {
        if (!event.defaultPrevented) start();
      }, 0);
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
    window.addEventListener('error', stop);
    window.addEventListener('unhandledrejection', stop);
    return () => {
      document.removeEventListener('click', onClick, true);
      document.removeEventListener('submit', onSubmit, true);
      window.removeEventListener('popstate', onPopState);
      window.removeEventListener(NAVIGATION_START_EVENT, onAnnounced);
      window.removeEventListener('pageshow', onPageShow);
      window.removeEventListener('error', stop);
      window.removeEventListener('unhandledrejection', stop);
    };
  }, []);

  useEffect(() => {
    if (started === null) return;
    const timer = window.setTimeout(() => setStarted(null), NAVIGATION_TIMEOUT_MS);
    return () => window.clearTimeout(timer);
  }, [started]);

  if (started === null) return null;

  return (
    <>
      <div
        aria-hidden="true"
        data-testid="navigation-progress"
        className="nav-progress-fade pointer-events-none fixed inset-x-0 top-0 z-[100] h-1 overflow-hidden"
      >
        <div className="nav-progress-bar h-full w-full bg-accent-ink" />
      </div>
      <div className="nav-progress-fade pointer-events-none fixed bottom-[calc(4.5rem+env(safe-area-inset-bottom))] start-1/2 z-[90] -translate-x-1/2 rounded-full border border-border bg-background p-2 shadow-lg lg:bottom-6">
        <Spinner size="md" />
      </div>
    </>
  );
}
