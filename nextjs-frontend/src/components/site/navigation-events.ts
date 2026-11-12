/**
 * Pure helpers for the global navigation indicator (navigation-progress.tsx): which clicks, form submits and
 * programmatic pushes are navigations to another page. Kept apart from the component so the rules are tested
 * directly.
 */

/** Window event a programmatic navigation dispatches (see `announceNavigation`). */
export const NAVIGATION_START_EVENT = 'freejoy:navigation-start';

/** Same-origin paths that are never a page: a click on one does not change the page, so it is ignored. */
const NON_PAGE_PATH_PREFIX = '/api/';

type CurrentLocation = Pick<Location, 'href' | 'origin' | 'pathname' | 'search'>;

/**
 * Path plus query: the part of the URL that identifies a page (a hash never loads one). The query goes through
 * URLSearchParams so `?q=a%20b` (the address bar) and `q=a+b` (`useSearchParams().toString()`) are the same key.
 */
export function routeKeyOf(pathname: string, search: string): string {
  const query = new URLSearchParams(search).toString();
  return query ? `${pathname}?${query}` : pathname;
}

/**
 * The destination when `rawUrl` is a different page of this site, else null: other origins, other protocols
 * (`mailto:`, `javascript:`), `/api/` paths, hash-only changes and the current URL are not navigations.
 */
function internalDestination(rawUrl: string, current: CurrentLocation): URL | null {
  let url: URL;
  try {
    url = new URL(rawUrl, current.href);
  } catch {
    return null;
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') return null;
  if (url.origin !== current.origin) return null;
  if (url.pathname.startsWith(NON_PAGE_PATH_PREFIX)) return null;
  if (routeKeyOf(url.pathname, url.search) === routeKeyOf(current.pathname, current.search)) {
    return null;
  }
  return url;
}

function opensInThisTab(target: string): boolean {
  return target === '' || target === '_self';
}

/** A primary-button click on a same-origin link to another page, without modifier keys, `target` or `download`. */
export function resolveClickDestination(event: MouseEvent, current: CurrentLocation): URL | null {
  if (event.defaultPrevented || event.button !== 0) return null;
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return null;
  if (!(event.target instanceof Element)) return null;

  const anchor = event.target.closest('a[href]');
  if (!(anchor instanceof HTMLAnchorElement)) return null;
  if (!opensInThisTab(anchor.target) || anchor.hasAttribute('download')) return null;

  return internalDestination(anchor.href, current);
}

/** A GET form submit whose result is another page of this site (the query string is built from the fields). */
export function resolveFormDestination(event: Event, current: CurrentLocation): URL | null {
  const form = event.target;
  if (!(form instanceof HTMLFormElement)) return null;

  const submitter = (event as Partial<SubmitEvent>).submitter ?? null;
  const method = (submitter?.getAttribute('formmethod') ?? form.getAttribute('method') ?? 'get')
    .trim()
    .toLowerCase();
  const target = submitter?.getAttribute('formtarget') ?? form.target;
  if (method !== 'get' || !opensInThisTab(target)) return null;

  const action = submitter?.getAttribute('formaction') ?? form.action;
  let url: URL;
  try {
    url = new URL(action || current.href, current.href);
  } catch {
    return null;
  }

  const query = new URLSearchParams();
  for (const [name, value] of new FormData(form)) {
    if (typeof value === 'string') query.append(name, value);
  }
  url.search = query.toString();
  return internalDestination(url.href, current);
}

/**
 * For the `onSubmit` of a `next/form` `<Form>` (see announcing-form.tsx): it always calls `preventDefault` and then
 * `router.push`, so the document-level submit listener cannot tell it from a form that script handles. The
 * destination comes from the native submit event, the same way as for a native GET form.
 */
export function announceFormNavigation(event: { nativeEvent: Event }): void {
  if (typeof window === 'undefined') return;
  const destination = resolveFormDestination(event.nativeEvent, window.location);
  if (destination) announceNavigation(destination.href);
}

/**
 * Tells the indicator that a navigation is starting, for code that calls `router.push` (there is no link
 * click to hear). `href` is optional; when given, the same checks as for a link apply.
 */
export function announceNavigation(href?: string): void {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new CustomEvent(NAVIGATION_START_EVENT, { detail: { href } }));
}

/** Whether an `announceNavigation` event is a real navigation (no `href`, or one that differs from this page). */
export function isAnnouncedNavigation(event: Event, current: CurrentLocation): boolean {
  const href = (event as CustomEvent<{ href?: string } | undefined>).detail?.href;
  return href === undefined || internalDestination(href, current) !== null;
}
