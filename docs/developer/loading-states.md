# Loading states and the page spinner (FREEJ-67)

Owner report: "The page spinner is missing for all the pages including admin pages." Whenever the person moves to
another page (link click, back or forward, a GET form such as the admin search or a `next/form` filter, `router.push`)
a visible spinner appears after 100 ms and stays until the new page has rendered. Three pieces do this.

## 1. The spinner: `src/components/ui/spinner.tsx`

`<Spinner size="sm|md|lg" label="Loading" />` is a ring drawn in `currentColor` with the `text-accent-ink` token, so it
reads on the light and the dark surfaces with no hard-coded colour.

| Property                       | How                                                                                                      |
| ------------------------------ | -------------------------------------------------------------------------------------------------------- |
| Announced once                 | `role="status"` (implicitly polite) with an `sr-only` "Loading" text; no `aria-label`, no `aria-live`    |
| Inside a status region already | `decorative` draws the ring only (`aria-hidden`, no role); `RouteSkeleton` and the indicator pill use it |
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
sits above a page, layout or template that calls `notFound()` or `redirect()`, and holds an exact allowlist of the ten
public segments that may have one, so a new one must be added on purpose. (A call hidden in a helper the file imports is
not detected; keep such decisions in the page.) The home page, `/freebies` and `/category/[slug]` get their loading cue
from the global indicator below and from their in-page Suspense skeletons.

A `loading.tsx` also does not help when the person moves between pages under one shared layout (admin Freebies to
Users): the boundary is already resolved, so React keeps the old page on screen until the new one is ready. That is
the second reason for the global indicator.

## 3. Global indicator: `src/components/site/navigation-progress.tsx`

Mounted once in `src/app/layout.tsx` (a client component inside its own `Suspense`, which `useSearchParams` needs).
A fixed 4px bar across the top of the window and a small spinner pill at the bottom centre (above the phone tab bar;
`z-40`, under dialogs). Both are `pointer-events-none` and `fixed`, so they never block a click or move the layout. The
component renders them only after `NAVIGATION_SHOW_DELAY_MS` (100 ms, a timer, so it also holds under reduced motion):
a navigation that finishes sooner, such as a prefetched page, never flashes the bar. One persistent `role="status"`
region (`sr-only`, empty until shown) announces "Loading". Nothing in Next.js 16 gives a global "navigation pending"
signal (`useLinkStatus` only works inside one `Link`), so it is built from events:

| Rule   | What                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| ------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Starts | Capture-phase click on a same-origin `<a>`: primary button, no modifier key, no `target` other than `_self`, no `download`, not `/api/...`, not the current path and query (so hash-only links are ignored). A native GET form submit (checked after the event, and only then are the fields read: `preventDefault` means script handles it). `popstate` to another path or query (both sides compared through `URLSearchParams`, so `%20` and `+` match). `announceNavigation(href?)` and `announceFormNavigation(event)` |
| Stops  | `usePathname()` plus `useSearchParams()` change (the new page has rendered); a page restored from the back-forward cache; the safety timeout `NAVIGATION_TIMEOUT_MS` (5 s)                                                                                                                                                                                                                                                                                                                                                 |
| Never  | On the first page load; for external links, `mailto:`, downloads, new-tab links or hash changes                                                                                                                                                                                                                                                                                                                                                                                                                            |

It does not listen for window `error` or `unhandledrejection`: any unrelated rejection would hide a live navigation. A
failed navigation ends in a new URL (the error page) or in the 5 s timeout. The timeout is short because outcomes that
end on the same URL (the admin bounce, a login redirect back) clear only there.

The decision rules are pure functions in `navigation-events.ts` (`resolveClickDestination`, `resolveFormDestination`,
`announceNavigation`, `announceFormNavigation`, `isAnnouncedNavigation`).

### `next/form` and `router.push`

`next/form` always calls `preventDefault` and then `router.push`, so the document-level submit listener cannot tell it
from a form that script handles. Use `AnnouncingForm` (`src/components/site/announcing-form.tsx`, a client wrapper that
passes `onSubmit={announceFormNavigation}`) instead of importing `Form` from `next/form`. The admin audit-log filters
and the merchant clicks form use it.

Next.js updates the address only when a navigation commits, so a bare `router.push()` gives the indicator nothing to
hear either. Code that pushes calls `announceNavigation(href)` first, on the path where the push happens. Wired:
sign-in (`use-complete-sign-in.ts`), sign-up, the admin offer, category and merchant forms, verify-email (three
redirects), the security page (back to `/login` after a password change, `/` after account deletion) and the
subscription success handler (four `router.replace('/dashboard')`). Every `router.push` and `router.replace` in `src`
is wired; `router.refresh()` is not a navigation. A new one needs the same one-line call.

### Known limits

- A click that script intercepts with `preventDefault` on an anchor, after the indicator's capture listener has run,
  looks like a Next `Link` click, so a link that only opens a dialog would show the indicator until the timeout. None
  exists today; use a `button` for such actions.
- `router.refresh()` and server actions do not change the URL and do not start the indicator (they have their own
  pending states).
- A navigation that ends on the same URL (for example a redirect back) clears at the timeout.

## Tests

- Unit: `spinner.test.tsx` (role, text, no aria-label, reduced-motion classes, token colour),
  `navigation-progress.test.tsx` (starts on internal links, GET forms, `next/form` through `announceFormNavigation`,
  back and forward and `announceNavigation`; ignores external, modifier, hash, `target`, download and `/api/` links;
  show delay; clears on URL change, on the timeout and on bfcache restore; not shown on first render; listeners removed
  on unmount; StrictMode), `announcing-form.test.tsx`, `verify-email-navigation.test.tsx`, announcement assertions in the
  sign-in, sign-up, admin form, security page and subscription handler tests (helper:
  `src/__tests__/unit/helpers/navigation-announcements.ts`), and `loading-boundary.test.ts`.
- E2E: `src/__tests__/e2e/loading-indicator.spec.ts` holds back the server answer with `page.route`, asserts the bar,
  the spinner and the one status region while it waits and that they are gone once the page has rendered (desktop only;
  the admin cases skip when `TEST_ADMIN_EMAIL` is not set). Run it with
  `npm run test:e2e:local -- loading-indicator` ([local-e2e.md](local-e2e.md)).
