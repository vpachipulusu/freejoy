import { afterEach } from 'vitest';
import { NAVIGATION_START_EVENT } from '@/components/site/navigation-events';

const cleanups: Array<() => void> = [];

afterEach(() => {
  while (cleanups.length > 0) cleanups.pop()?.();
});

/**
 * Records every `announceNavigation()` call (the global loading indicator's start signal) made while a test
 * runs. Call it at the top of a test; the listener is removed after each test.
 */
export function recordNavigationAnnouncements(): { hrefs: Array<string | undefined> } {
  const hrefs: Array<string | undefined> = [];
  const listener = (event: Event) => {
    hrefs.push((event as CustomEvent<{ href?: string } | undefined>).detail?.href);
  };
  window.addEventListener(NAVIGATION_START_EVENT, listener);
  cleanups.push(() => window.removeEventListener(NAVIGATION_START_EVENT, listener));
  return { hrefs };
}
