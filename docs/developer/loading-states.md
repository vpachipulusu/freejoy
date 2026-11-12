# Loading states and the page spinner (FREEJ-67)

Owner report: "The page spinner is missing for all the pages including admin pages." Whenever the person moves to
another page (link click, back or forward, a GET form such as the admin search, `router.push`) a visible spinner
appears quickly and stays until the new page has rendered. Three pieces do this.

## 1. The spinner: `src/components/ui/spinner.tsx`

`<Spinner size="sm|md|lg" label="Loading" />` is a ring drawn in `currentColor` with the `text-accent-ink` token, so it
reads on the light and the dark surfaces with no hard-coded colour.

| Property                       | How                                                                                                      |
| ------------------------------ | -------------------------------------------------------------------------------------------------------- |
| Announced once                 | `role="status"`, `aria-live="polite"`, `aria-label="Loading"` plus an `sr-only` "Loading" text           |
| Inside a status region already | `decorative` draws the ring only (`aria-hidden`, no role); `RouteSkeleton` uses it                       |
| Reduced motion                 | Spins under `motion-safe:animate-spin`; `motion-reduce:animate-none` leaves a static ring, still visible |
| No layout shift                | The size comes from the `size` prop and never changes                                                    |

## 2. Route loading states: `loading.tsx` with `RouteSkeleton`

`src/components/site/route-skeleton.tsx` shows the spinner above skeleton blocks (the skeleton keeps the layout from
jumping). These segments re-export it as `loading.tsx`: `(admin)/admin`, `(auth)`, `dashboard`, `pricing`, and the
public pages `about`, `affiliate-disclosure`, `alerts`, `contact`, `cookies`, `developers`, `partners`, `privacy`,
`saved`, `terms`.

**There is deliberately no `(public)/loading.tsx`, no root one, and none in `freebies` or `category`.** A `loading.tsx`
covers its own segment and everything below it, and wraps the page in a Suspense boundary. The dynamic pages
`/freebies/[slug]` and `/category/[slug]` call `notFound()` after reading the catalogue; with a boundary above them the
shell has already been sent with status 200, so every unknown offer or category becomes a soft 404
(see [freejoy-seo.md](freejoy-seo.md)). `src/__tests__/unit/config/loading-boundary.test.ts` fails when a loading file
sits above a page that calls `notFound()`. The home page, `/freebies` and `/category/[slug]` get their loading cue
from the global indicator below and from their in-page Suspense skeletons.

A `loading.tsx` also does not help when the person moves between pages under one shared layout (admin Freebies to
Users): the boundary is already resolved, so React keeps the old page on screen until the new one is ready. That is
the second reason for the global indicator.

## 3. Global indicator: `src/components/site/navigation-progress.tsx`

Mounted once in `src/app/layout.tsx` (a client component inside its own `Suspense`, which `useSearchParams` needs).
A fixed 4px bar across the top of the window and a small spinner pill at the bottom centre (above the phone tab bar).
Both are `pointer-events-none` and `fixed`, so they never block a click or move the layout. A 100 ms fade-in delay
(`.nav-progress-fade` in `globals.css`) stops a navigation that finishes at once from flashing the bar. Nothing in
Next.js 16 gives a global "navigation pending" signal (`useLinkStatus` only works inside one `Link`), so it is built
from events:

|        | Rule                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Starts | Capture-phase click on a same-origin `<a>`: primary button, no modifier key, no `target` other than `_self`, no `download`, not `/api/...`, not the current path and query (so hash-only links are ignored). A GET form submit that script does not handle (checked after the event: `preventDefault` means script handles it), whose action is the same origin and differs from the current URL. `popstate` to another path or query. `announceNavigation(href?)` (see below) |
| Stops  | `usePathname()` plus `useSearchParams()` change (the new page has rendered); a window `error` or `unhandledrejection`; a page restored from the back-forward cache; the safety timeout `NAVIGATION_TIMEOUT_MS` (10 s)                                                                                                                                                                                                                                                          |
| Never  | On the first page load; for external links, `mailto:`, downloads, new-tab links or hash changes                                                                                                                                                                                                                                                                                                                                                                                |

The decision rules are pure functions in `navigation-events.ts` (`resolveClickDestination`, `resolveFormDestination`,
`announceNavigation`, `isAnnouncedNavigation`).

### Programmatic navigation

Next.js updates the address only when a navigation commits, so a bare `router.push()` gives the indicator nothing to
hear. Code that pushes after async work calls `announceNavigation(href)` first. Done for sign-in
(`use-complete-sign-in.ts`), sign-up and the admin offer, category and merchant forms. Other `router.push` calls
(verify-email, the security page) are not wired; add the one-line call when one needs it.

### Known limits

- A click that script intercepts with `preventDefault` on an anchor looks like a Next `Link` click, so a link that only
  opens a dialog would show the indicator until the timeout. None exists today; use a `button` for such actions.
- `router.refresh()` and server actions do not change the URL and do not start the indicator (they have their own
  pending states).
- A navigation that ends on the same URL (for example a redirect back) clears at the timeout.

## Tests

- Unit: `spinner.test.tsx` (role, name, reduced-motion classes, token colour), `navigation-progress.test.tsx` (starts on
  internal links and GET forms, ignores external, modifier, hash, `target`, download and `/api/` links, clears on URL
  change, on the timeout and on errors, not shown on first render), `loading-boundary.test.ts`.
- E2E: `src/__tests__/e2e/loading-indicator.spec.ts` holds back the server's answer with `page.route`, asserts the
  indicator while it waits and that it is gone once the page has rendered (public pages; the admin cases skip when
  `TEST_ADMIN_EMAIL` is not set). Run it with `npm run test:e2e:local -- loading-indicator` ([local-e2e.md](local-e2e.md)).
