# Freejoy frontend

Phase 1 built the public Freejoy pages on fixture data so the design could be approved. FREEJ-21
(Phase 2) switched them to real published offers from the catalogue API. The pages are still server
rendered and need no login, the browser never calls FastAPI, and the fixtures remain for local design
work and unit tests. Nothing here stores visitor data on a server or links to an external site. The one
thing kept in the browser is the visitor's own Saved list (FREEJ-28, [Saved freebies](#saved-freebies-freej-28)).

Design reference: `design-reference/freejoy/` (PNG and SVG screens plus `design_tokens.json`).
Plan and decisions: `.claude/plans/freejoy-phase1.plan.md`. Jira: project `FREEJ`, stories
FREEJ-6 to FREEJ-16 (Phase 1), FREEJ-21 (the switch to the API) and FREEJ-28 (favourites).

## Routes

All public pages live in the `(public)` route group under `src/app/(public)/` and share
`layout.tsx` there (skip link, announcement bar, header, footer, mobile tab bar).

| Route | Screen | Notes |
|---|---|---|
| `/` | 01 / 05 | Hero, trust strip, category grid, "Freshly found" cards, newsletter band |
| `/freebies` | 02 / 06 | Search, category/type/postage filters, sort, pagination, empty and error states |
| `/category/[slug]` | 02 | Category intro plus the same catalogue controls scoped to one category |
| `/freebies/[slug]` | 03 / 07 | Hero, claim steps, "important bits", trust panel, related offers; the "offer has ended" page for an ended offer; 404 page for an unknown one |
| `/saved` | 05 / 06 tab | The visitor's saved freebies, read from this device ([Saved freebies](#saved-freebies-freej-28)); static shell, `noindex, follow`, not in the sitemap |
| `/alerts` | 04 | Preferences form; starts a double opt-in through a server action ([backend-subscribers.md](backend-subscribers.md)); a plain "not open yet" notice until `ALERTS_FORM_LIVE=true` |
| `/partners` | – | "List your free sample" for retailers (FREEJ-55): static page in the same draft shell as the legal pages (banner, `noindex` while draft), no submission form; asks for no details; linked from the footer ("For retailers") and `/contact`, not in the sitemap while draft |
| `/developers/partner-api` | – | The partner API documentation for retailers (FREEJ-59): the endpoint list, parameters and status codes are built at build time from the committed partner OpenAPI document (`public/partner-openapi.json`, offered for download at `/partner-openapi.json`), the prose and examples are `src/lib/partner-api/content.ts` keyed by operation id, and a test fails when the two disagree ([backend-partner-api.md](backend-partner-api.md#the-public-documentation-page)). Same draft shell and `noindex` as `/partners`, linked from `/partners`, not in the sitemap; examples hold no real key, merchant or id |
| `/about`, `/contact`, `/privacy`, `/cookies`, `/terms`, `/affiliate-disclosure` | – | Editorial and legal scaffolding, each with a visible "draft, pending legal review" banner |
| `not-found.tsx` | – | Freejoy-styled 404 |

`/`, `/freebies`, `/category/[slug]`, `/freebies/[slug]`, `/alerts` and `/sitemap.xml` export
`dynamic = 'force-dynamic'` (or read the query string, which has the same effect). They render per
request, so `next build` never calls the backend and an outage can never be baked into a prerendered
page; the cache that protects the backend is the Data Cache described under
[Catalogue data](#catalogue-data) below. Indexing follows the catalogue source and the data; see
[freejoy-seo.md](freejoy-seo.md).

## Catalogue data

`src/lib/catalogue/` is the only data source. Pages and components import the facade
`@/lib/catalogue` (never `fixtures.ts`, `api-source.ts` or the API wrapper directly), and every facade
function answers from the configured source:

| `CATALOGUE_SOURCE` | Source | When |
|---|---|---|
| unset | the catalogue API | default for `next dev`, `next build` and production |
| unset under `NODE_ENV=test` | fixtures | the default for Vitest, so unit tests stay offline |
| `api` | the catalogue API | explicit |
| `fixture` | 24 fixture offers, 6 categories, 8 merchants | local design work without a backend; every page then shows the demo badge and wording and stays `noindex` |

The variable is server-only, read at call time by `source.ts` (`getConfiguredSource()`), and listed in
`nextjs-frontend/.env.example`. An unknown value is treated as unset. `getCatalogueSource()` (async)
returns the source and is what the SEO code asks; it does not depend on what is live. The Playwright
e2e server runs with `CATALOGUE_SOURCE=fixture` unless `E2E_LIVE_CATALOGUE=1`
(`scripts/e2e-env.cjs`).

Files in `src/lib/catalogue/`:

- `types.ts`: `Offer`, `Category`, `Merchant`, `CatalogueQuery`, `Page<T>` and the label maps. An API
  offer carries its `merchant` (the API has no merchant list); fixture offers resolve it by slug.
- `index.ts`: the facade. `lookupOffer(slug)` returns `found`, `ended` (the API's 410) or `not_found`;
  `getOfferBySlug(slug)` is the found-or-null shortcut.
- `source.ts`: `CatalogueSource`, `getConfiguredSource()`, the `CatalogueBackend` interface both sources
  implement, and the `OfferLookup` / `EndedOffer` types.
- `fixture-source.ts` and `fixtures.ts`: the fixture implementation. Every fixture offer has
  `demo: true`; two are expired and one is a draft so those states can be previewed. The clock is the
  fixed `FIXTURE_TODAY` (2026-10-29).
- `api-source.ts`: the API implementation. Reads go through the wrapper, every record passes
  `api-mapper.ts`, and reads that one render repeats (metadata, heading and results) are memoised per
  request with React `cache`. `getToday()` is the **Europe/London calendar date** (`today.ts`), because
  `isLive` and the 30-day expiry policy compare calendar days.
- `api-mapper.ts` and `slug.ts`: the facade boundary. Slugs (offer, category, merchant) must match
  `^[a-z0-9-]+$`; a record with any other slug is dropped and logged (the value is JSON-quoted and cut to
  60 characters). An offer with no verification date, an unreadable date or an unknown type is dropped
  too: the page says "Last checked", so it never invents one. Timestamps become London calendar dates.
  An unknown tint falls back to `cream`, an unreadable postage to "check terms".
- `errors.ts`: `CatalogueUnavailableError`, `settle()` and the curated visitor message.
- `query.ts`: filtering, sorting, pagination, related offers, `isLive` (fixtures and the pure helpers).
- `offer-url.ts`: the `/freebies?q=&category=&type=&postage=&sort=&page=` codec. Every catalogue
  control is a link or GET form, so filter state lives in the URL and works without JavaScript. A
  category in the URL only has to be a well-formed slug; the source decides whether it exists (the API
  answers an empty list, the fixtures ignore it).
- `revalidate.ts`: `revalidateCatalogue(paths?)`, see below.

## Catalogue API wrapper (FREEJ-21)

`src/lib/api/catalogue.ts` (`import 'server-only'`) is the only caller of the generated
`listOffers`, `readOffer` and `listCategories` (ESLint forbids importing the generated client anywhere
else). Contract: [backend-catalogue.md](backend-catalogue.md).

- **Calls.** `fetchOffers(params)`, `fetchOffer(slug)` and `fetchCategories()` go through
  `callBackend(..., { withSession: false, shared: true })`: the internal key, never the visitor's session,
  never a cookie read.
- **Cache.** Each read runs inside `unstable_cache` with tag `catalogue` and `revalidate: 60`
  (`CATALOGUE_CACHE_TAG`, `CATALOGUE_REVALIDATE_SECONDS`). A page costs the backend one request a minute
  per distinct read however many visitors arrive. A search with text (`q`) **skips the cache** so a
  visitor's words are never a cache key (and are never logged); it is still rate limited per visitor.
- **Timeout.** 3 s per request (`CATALOGUE_REQUEST_TIMEOUT_MS`, `AbortSignal.timeout`), as in
  `settings.ts`.
- **Errors are thrown, never cached.** Any outcome other than success, 404 or 410 (unreachable, timeout,
  401, 422, 429, 5xx) logs a warning (operation, status, backend message; no search text) and throws
  `CatalogueUnavailableError`. `unstable_cache` stores nothing for a thrown error, so the next request
  tries again, and a stale entry keeps being served while a background refresh fails. A 404 becomes
  `not_found` and a 410 becomes `gone` with the stub (title, category, end date); those are real answers
  and may be cached. A 404 counts as `not_found` only when its body is the backend's own envelope
  with `code: "offer_not_found"` (`CodedHTTPException` in `fastapi_backend/app/api/v1/catalogue.py`), and a 410 as
  `gone` only when it is the backend envelope with `expired: true`; the same status from anything else in the path
  (a proxy's HTML page, the backend's route-level "Not Found") throws. Never branch on the error text. A 429 throws
  `CatalogueRateLimitedError`, a subclass of `CatalogueUnavailableError`, so callers that fan out reads (the Saved
  action) can stop at once.
- **Visitor IP.** The backend rate limit is 120 requests a minute per `X-Client-IP`. `readTrustedClientIp()`
  in `server.ts` reads Vercel's `x-vercel-forwarded-for` (only when `VERCEL=1`) and **one** header named by the
  server-only `TRUSTED_CLIENT_IP_HEADER` (no default; the name must match `/^[a-z0-9-]+$/i`), never
  `X-Forwarded-For` or any other header a client can set, and only if the value is a single IP address.
  Set `TRUSTED_CLIENT_IP_HEADER` only when your reverse proxy overwrites that header on every request;
  otherwise clients can spoof rate-limit keys. `x-forwarded-for`, `forwarded`, `via` and
  `x-vercel-forwarded-for` are refused as its value (a one-time warning is logged), and a value with an IPv6
  zone (`fe80::1%eth0`) or brackets is not forwarded. This only fixes the key the **backend** limiter sees:
  the edge proxy limiter (`client-identifier.ts`) still buckets non-Vercel visitors together. The wrapper reads it before the cache boundary and carries it to the request in an
  `AsyncLocalStorage`, so it is **not** part of the cache key (one entry per read, not per visitor) and is
  used only when the read actually reaches the backend. `shared: true` still reads no request headers
  itself; the address arrives through the new `clientIp` option. With no trusted header the header is
  omitted. Checked by hand against a local stub backend with `VERCEL=1` and pinned by `catalogue-headers.test.ts`.
- **Rate-limit caveats.** Without a trusted header (non-Vercel hosting with `TRUSTED_CLIENT_IP_HEADER`
  unset) every read shares **one** backend bucket (`public_catalogue`, 120 a minute, keyed on the
  server's egress IP). The cache absorbs normal traffic, but a flood of distinct searches (which skip the
  cache) can exhaust that bucket and put the whole site into its error state. `VERCEL=1` must never be
  set on a non-Vercel host (a test keeps it out of the env templates). A CDN in front of Vercel makes
  every visitor share the CDN's address unless the header you trust carries the real one.
- **No backend text in the browser.** Pages show the curated `CATALOGUE_UNAVAILABLE_MESSAGE`.

`revalidateCatalogue(paths?)` (`src/lib/catalogue/revalidate.ts`, server-only) expires the `catalogue`
tag immediately (`revalidateTag('catalogue', { expire: 0 })`) and optionally revalidates paths (a no-op for the force-dynamic public pages, kept for the FREEJ-22 contract; paths must be plain site paths). It is for
the admin publish, unpublish and expire actions (FREEJ-22); nothing calls it yet.

## States

| State | What the visitor gets |
|---|---|
| Loading | `RouteSkeleton` `loading.tsx` for the admin, auth, dashboard and pricing segments only (none above the public pages, see Not found); in-page Suspense skeletons for the home sections and the catalogue results, `role="status" aria-busy="true"` with an accessible name, blocks `aria-hidden` |
| Empty | "No freebies match that yet" with a Clear filters link; "no featured freebies right now" on the home page |
| Error | `InlineError` (`role="alert"`, curated copy) with a "Try again" button (`RetryButton`, a real `button` that calls `router.refresh()`, `aria-disabled` while it runs so focus stays). The page answers **HTTP 200** for the duration of the outage and keeps its normal robots (a 5xx is a follow-up, see [freejoy-seo.md](freejoy-seo.md#when-the-catalogue-cannot-be-read)); it is never cached: dynamic responses carry `Cache-Control: private, no-cache, no-store`. |
| Not found | `notFound()` for an unknown, unpublished or non-slug offer or category answers a real **HTTP 404**. This holds only because no `loading.tsx` sits above the public pages; `loading-boundary.test.ts` guards it. |
| Ended | The API's 410 renders "This offer has ended" with the title, the end date and two ways onward (no call to action, claim steps or terms). `noindex, follow` for 30 days after the end date, then not found. |

## Trust rules the UI follows

- Fixture (demo) data is labelled on every surface: card badges, the "Sample listings" copy on the home
  and catalogue pages, the hero card, the alerts notice and the offer CTA. **API offers carry no demo
  badge or wording** (`offer.demo` is false; pages ask `getCatalogueSource()`).
- "Last checked" and freshness labels come only from the offer's `lastVerifiedAt` and `freshness`. There
  are no visitor counts, testimonials, scarcity cues or verification claims.
- A demo offer never says "Last checked": the trust panel calls its date a "Sample date" and says Freejoy has
  not checked the offer.
- `displayFreshness()` (`src/lib/catalogue/freshness.ts`) decides which freshness label a card may show. "Popular",
  "Ends soon" and "Limited" are editor-set claims with no evidence behind them, so only a demo fixture shows
  them; a real (API) offer shows "New today" and "Just added" only. To show one on a real offer, add a
  documented rule there first (checklist C1, C2) and a test.
- Buttons and chips do not call a sample or a trial "free" ("Request this sample", "Start the trial", chips
  "SAMPLE" and "TRIAL"). `ctaConditions()` in `offer-format.ts` renders the postage (samples) or the card
  requirement (trials) in a line right after the button, from the offer's own fields.
- Site-wide copy (announcement bar, tagline, hero, trust strip) makes no zero-cost, "daily" or quality claim.
  `public-copy.test.tsx` pins this. A `free_sample` card shows its postage on its own line under the title. Why each line reads as it does: `docs/compliance/uk-affiliate-checklist.md`
  section I.3.
- A demo (fixture) offer has no destination, so its CTA opens a "Demo offer" dialog. A real offer's CTA links
  to the first-party `/go/<offerId>` redirect (FREEJ-23). The "Ad" label before the button and the commission
  sentence after it appear unless the offer's `earnsCommission` is false (FREEJ-60: the offer earns commission when its basis
  is affiliate or the followed link is on a network); an offer that earns nothing shows neither and its link carries
  `?nd=1`. An unknown flag (null) discloses. No element on any page has an external `href`.
- The alerts form validates with Zod, requires consent, and submits through a server action
  (FREEJ-24). It answers "check your inbox" the same way for every address, and nothing starts until
  the visitor opens the emailed link and presses the button on `/alerts/confirm`. Until
  `ALERTS_FORM_LIVE=true` it shows a plain "Alerts aren't open yet" notice instead. No address or token
  is ever logged.
- Legal pages carry a draft banner and a reviewer checklist instead of compliance claims.
- Public pages set no cookies. They use no browser storage either, except the visitor's own Saved list
  (`freejoy.saved.v1`, written only when a heart is pressed; `lib/saved/saved-store.ts` is the only code in
  the app that touches browser storage, and `public-pages-privacy.test.tsx` fails if that changes).
  `catalogue-live.spec.ts` and `saved.spec.ts` check `context.cookies()`.

## Saved freebies (FREEJ-28)

A heart on every offer card and on the offer page saves the offer to a list on **this device**; `/saved`
lists them; the phone tab bar has a Saved tab and the desktop header has a Saved link. No account, no
cookie, no backend call to store anything, and **no analytics event on save or unsave**.

**Storage contract** (`src/lib/saved/saved-store.ts`; unit tests in `unit/lib/saved/`):

| | |
|---|---|
| Medium | `window.localStorage`, one key: `freejoy.saved.v1` |
| Value | `{"v":1,"slugs":["glow-boosting-face-cream-sample", ...]}`: offer **slugs** only, oldest first. No titles, no ids of people, nothing else |
| Read | Defensive. Not that envelope (bad JSON, another version, over 32 KB) means an empty list, never a throw; a bad entry inside a good envelope (not a valid slug, a duplicate) is dropped. A read never rewrites the stored value; the next save replaces it |
| Cap | 200 (`MAX_SAVED_OFFERS`). Saving past it drops the oldest |
| Storage unavailable | Private mode, blocked site data, quota: reads see an empty list, writes keep the list in memory for that page, so the heart still toggles until the tab closes. A successful write from another tab ends the memory fallback |
| Consent | This is functional storage the visitor asked for by pressing the heart, so it needs no consent banner (our reading of PECR's "strictly necessary" exemption, **to be confirmed in legal review**). It is listed in the draft cookie notice and mentioned in the draft privacy notice; both stay marked draft |

If the shape ever changes, add `freejoy.saved.v2` and migrate or discard `v1`; never reuse the key.

**React side.** The store is an external store for `useSyncExternalStore` (`subscribeSaved`,
`getSavedSnapshot`, `getServerSavedSnapshot`; hooks in `src/lib/saved/use-saved.ts`). The snapshot is a frozen
array that keeps its identity until the stored value changes. The server snapshot is empty, so the server render
and the first client render agree and there is no hydration mismatch. Every heart and the Saved page share the
store, and the `storage` event keeps other tabs in step. `useHydrated()` tells "not read yet" (skeleton) from
"nothing saved" (empty state), so a saved list never flashes empty.

**The heart** (`SaveButton`). A real `<button type="button">` with `aria-pressed` and the constant name
"Save {title}" (the title is never stored; `aria-pressed` carries the state). 44px target around a 32px disc (so it fits a narrow
two-column card; the badge on the card shrinks on phones for the same reason). A heart that is outlined or
filled (so state is not only a colour); no focus style of its own, the global outline shows. Motion is
`motion-safe:` only. It sits **beside** the card's stretched link, never inside it, with `z-10` to stay
clickable. `OfferCard` and `OfferHero` stay server components; the heart is the only client island in them.
On the card the demo badge moved to the bottom-right to free the corner the design gives the heart.

**The Saved page** (`src/app/(public)/saved/page.tsx`). A static server shell (heading, the "Saved on this
device" note, a `<noscript>` message) around the `SavedOffers` client island. The island reads the slugs and
calls the server action `loadSavedOffersAction` (`src/app/actions/saved-actions.ts`) in chunks of 24
(`SAVED_OFFERS_REQUEST_LIMIT`), once per slug (a slug saved later, say in another tab, is the only one asked for
next). It renders the **same `OfferGrid` and `OfferCard`** as the catalogue, newest saved first, with a polite
live region for the count and, when there is more, a "Show more" button (see below for focus). The island sits in a
box with a minimum height so the footer does not jump.

**The server action.** Input validated with Zod: 1 to 24 slugs, each `^[a-z0-9-]+$` and at most 100 characters,
otherwise a curated failure and no catalogue read. Each slug goes through `lookupOffer` (the same facade and
60-second Data Cache as the offer page), at most 4 at a time, and ends in exactly one list:

- `offers`: published and live today (`isLive`; an expired offer is not shown even though the facade still
  looks it up as found). A card-shaped projection only; fixture offers get their merchant attached.
- `missing`: a definitive no (the backend's 404 envelope, a 410, an answer for another slug, past its end date). The page **drops these from the list and from
  storage** and says "N saved freebie(s) no longer available and removed from your list". No error.
- `unavailable`: the catalogue could not be read, was rate limited, returned a record this site cannot read, or the call stopped before reaching the slug. **Never pruned**, so an outage cannot empty a list. The page
  says they are still saved and offers "Try again"; a failed call shows a curated error and also waits for the
  visitor (no automatic retry loop).

The action logs the error name only, never a slug.

**Cost to the backend and its limits.** The action is public and unauthenticated, and the backend allows 120
reads a minute per address (one bucket for every visitor when no trusted client-IP header is configured, see
[Catalogue API wrapper](#catalogue-api-wrapper-freej-21)). Server actions are not covered by the edge rate
limiter (it covers `/api/` and `/go/`), so the action meters itself:

- 24 slugs a call (`SAVED_OFFERS_REQUEST_LIMIT`), 4 reads at a time;
- a budget of backend **reads**, not calls (`rateLimit` with `cost = slugs.length`, charged after validation and
  de-duplication, before any read or date lookup): a visitor with a trusted address gets **72 reads a minute**
  (scope `saved`, keyed on the IPv6 /64 or IPv4 address, `IPv4-mapped` as IPv4, as the backend does); with no
  trusted address everyone shares **60 reads a minute in total** (scope `saved-shared`), so Saved can never take more
  than half the backend's 120 and degrades softly (the curated failure, nothing pruned) rather than one client
  locking the page for everyone. A refusal logs a count only (never an address or a slug), and in production the
  action warns once per process that there is no trusted client address. **Set `TRUSTED_CLIENT_IP_HEADER` in
  production off Vercel** (the repo's own Dockerfile is such a host; see `.env.example`). The limiter is in
  memory, per server instance (several instances multiply the budget), and holds at most 50,000 buckets;
- the first 429 from the backend (`CatalogueRateLimitedError`), 4 `unavailable` results in a row (any answer
  resets the count), or about 8 seconds overall, stop the call taking new slugs; every remaining slug comes back
  `unavailable` with no further backend call by this action. That holds for cache misses only: a stale cache entry
  is served and refreshed in the background by Next.js, which can cost the backend one read of its own. So a dead
  backend costs about 8 reads or fewer, not 24.

The browser loads **one chunk on demand**: the newest 24 saved slugs, then 24 more each time "Show more" is pressed
(focus moves to the first new card). 200 saved offers are one call until then. A chunk that comes back with no
offer and nothing missing marks every slug not yet asked about unavailable and stops (no drain). An answer that
leaves a slug out is treated as unavailable, never as an endless wait. A cold cache therefore costs at most 24
reads per page view, and a pruning sweep of a long stale list is at most one call per 24 slugs.

**What may prune.** Only a definitive answer: `lookupOffer` says `not_found` (the backend's 404 with
`code: "offer_not_found"`) or `ended` (its 410 envelope with `expired: true`), or the offer is past its end date.
`lookupOffer` also has an `unreadable` kind (the API has the record but the mapper cannot read it, for example a
freshness value this site does not know yet): that is `unavailable`, and the offer page treats it like a 404. An
answer for another slug is a fault, so also `unavailable`. A 404 or 410 that is not the backend's own envelope
throws `CatalogueUnavailableError` in `lib/api/catalogue.ts`. **Deploy order:** the backend that sends
`offer_not_found` must be live before this frontend; until then a genuinely unknown offer answers the error state
instead of a 404 (nothing is pruned).

**What leaves the server.** A card-shaped projection (`CardOffer` in `lib/catalogue/types.ts`: id, slug, title,
offer type, image, postage, freshness, demo, source, merchant), not the whole offer; `OfferCard` and `OfferGrid` take
that type.

**Accessibility notes.** The heart's name is the constant "Save {title}" and `aria-pressed` carries the state (a
label must not flip with it). Focus after a removal: a heart pressed in the list moves focus to the next card's
heart (the previous one if it was last, the "Nothing saved yet" heading if none are left); a change from another tab
never moves focus; "Try again" moves focus to the count region, which then says "Loading your saved freebies". The
list has a visually hidden `h2` so the headings run 1, 2, 3. The count says "{shown} of {total} saved freebies shown"
whenever something is hidden or unavailable.

**Monitoring.** The heart's name is an offer title, so `instrumentation-client.ts` registers `scrubBreadcrumb`
(a click breadcrumb on `aria-label="Save ..."` loses its message) and `scrub-event.ts` turns
`/v1/catalogue/offers/<slug>` in span URLs into `/v1/catalogue/offers/[slug]`. Unit-tested on constructed events only.

**Navigation.** `SITE_TAB_ITEMS` is Home, Browse, **Saved**, Alerts, as in the design (screens 05 to 07): Saved
took the third slot, so Categories left the phone tab bar (it stays in the menu sheet, on the home page and on
`/freebies`). The active tab gets `aria-current="page"` from `activeTabHref`. The desktop header has a heart link
to `/saved` next to Log in (icon only at 1024px, with the word from 1280px, so the nav row never wraps). There is
no count badge, because the design has none.

**Indexing and routing.** `/saved` is `noindex, follow` (it is different on every device), is not in the
sitemap and is left crawlable in `robots.txt` so the `noindex` can be seen. `src/proxy.ts` keeps no list of
public routes: it protects `/dashboard`, `/settings`, `/profile`, `/security` and `/admin` and meters `/api/`
and `/go/`, so `/saved` is public without an entry (`saved-page.test.tsx` and the proxy test cover it).

## Theme and typography

- `src/config/themes.ts`: the `freejoy` preset is the default. Tokens map `design_tokens.json`
  to HSL CSS variables (`--cta` coral, `--accent-ink` green, `--secondary` cream, `--accent` mint).
  The CTA was lifted slightly from the token value so ink-on-coral passes 4.5:1
  (`theme-contrast.test.ts` enforces this).
- `src/config/fonts.ts`: Inter 700/800 as `--font-display`; body font stays admin-selectable.
- `tailwind.config.ts`: `rounded-input` 12px, `rounded-card` 16px, `rounded-editorial` 24px,
  `rounded-pill`, `max-w-content` 1314px.

## Components

| Folder | Contents |
|---|---|
| `src/components/site/` | Header, footer, mobile tab bar, announcement bar, search form, brand mark |
| `src/components/saved/` | `SaveButton` (the heart), `SavedOffers` (the Saved page's list) and its `useSavedOffers` hook |
| `src/components/catalogue/` | `OfferCard`/`OfferGrid` (shared), filters, sort, pagination, empty state, drawer, `InlineError`, `RetryButton` |
| `src/components/home/` | Hero, hero art, trust strip, category grid, freshly found, newsletter band, `HomeSections` (+ skeleton) |
| `src/components/offer/` | Breadcrumbs, hero, claim steps, important bits, trust panel, CTA, ended panel and ended-offer page, related |
| `src/components/alerts/` | `AlertsForm` (client component, react-hook-form + Zod) |
| `src/components/legal/` | `LegalPage` shell with draft banner and `ReviewerChecklist` |

Only `offer-cta.tsx`, `retry-button.tsx`, `alerts-form.tsx`, `filter-drawer.tsx`, the mobile tab bar, the
site header and the two Saved components (`save-button.tsx`, `saved-offers.tsx`) are client components. Client components must not import `@/lib/catalogue` (the facade),
`@/lib/api/catalogue` or read `BACKEND_*` / `CATALOGUE_SOURCE`; the client/server boundary test enforces
it. The filter drawer and sort control render outside the keyed Suspense boundaries in
`catalogue-layout.tsx` so they keep focus across filter navigations.

## Tests

- Saved freebies: `unit/lib/saved/saved-store.test.ts` (add, remove, toggle, cap, corrupt and foreign data,
  throwing storage, the `storage` event, the server snapshot), `unit/components/save-button.test.tsx` (heart,
  hydration), `saved-offers.test.tsx` (list, empty, pruning, outage, chunking), `saved-navigation.test.tsx` (tab bar
  and header), `unit/actions/saved-actions.test.ts` (validation, bounds, only published offers) and
  `unit/app/saved-page.test.tsx` (page, noindex, sitemap, robots), `unit/lib/saved/load-saved-chunks.test.ts`
  (the chunk loop) and the Sentry scrub tests for the heart and offer slugs.
- Unit (Vitest): `src/__tests__/unit/app/*` render each page and check metadata, 404s, error states and
  the no-external-link rule; `unit/api/catalogue*.test.ts` cover the wrapper (cache wiring, status
  mapping, timeout, outage error, headers on the wire); `unit/lib/catalogue/*` cover the mapper, the
  source switch, the London date, the API source and the fixtures; `unit/components/*` cover the header,
  cards, catalogue controls, CTA, error and live-versus-demo wording; `unit/config/client-server-boundary`
  keeps backend settings out of the browser.
- Playwright specs exist for the homepage, catalogue, offer detail, alerts and display font
  (`src/__tests__/e2e/*.spec.ts`, fixture catalogue) and `catalogue-live.spec.ts` (real data, journeys 1
  and 5). They need `npm run test:e2e:local`, which builds a production bundle and expects a running
  backend and a Firebase test project. **`catalogue-live.spec.ts` was written in FREEJ-21 but not run**: it
  needs a seeded backend (FREEJ-20). How to run it is in the file header and in
  [local-e2e.md](local-e2e.md#real-catalogue-data). `saved.spec.ts` (FREEJ-28: save, open `/saved`, remove, reload,
  corrupt storage, a vanished offer, per-browser isolation; desktop and 390px) runs on the fixture catalogue, like
  `freebies.spec.ts`. Run it with `npm run test:e2e:saved` (`playwright.saved.config.ts`: no login, no backend, no
  `webServer`) against a production build you serve yourself with `CATALOGUE_SOURCE=fixture`,
  `E2E_BASE_URL`. The Saved action's read budget is keyed on the trusted client address, so each test sends its
  own (`x-e2e-client-ip`); the server believes that header only in a local e2e build (`NEXT_DIST_DIR=.next-e2e`,
  `E2E_RATE_LIMIT_TEST_MODE=1`, a local host: `src/lib/middleware/local-e2e.ts`, the same test the edge limiter
  uses). `scripts/e2e-env.cjs` (`toNextProcessEnv`, used by `e2e-build.mjs`, `e2e-start.mjs` and the Playwright
  `webServer`) sets all three, so use those scripts; an ordinary build ignores `TRUSTED_CLIENT_IP_HEADER=x-e2e-client-ip`. It also checks the header at 1024, 1280 and 1366px, the card
  badge at 320 to 375px, hover isolation and scroll position. All 20 tests passed that way three times in a row
  when the FREEJ-28 review fixes were made; it has **not** been run through `test:e2e:local` or in CI
  (E2E is not in the gate).

## Known deviations

- (Fixed in FREEJ-24.) The newsletter band used to be a GET form to `/alerts?email=`; it is now a
  plain link to `/alerts` and `/alerts` ignores any `email` parameter, so an address never travels in a
  page address. What is still true, and what Sentry does about it, is in
  [Error monitoring and addresses](#error-monitoring-and-addresses-freej-31).
- The mobile filter drawer needs JavaScript; search, sort (Apply button), category chips and
  pagination work without it.
- An outage answers HTTP 200 (error state, normal robots) for as long as it lasts; a 5xx is a follow-up.
- The offer page has no route-level loading skeleton: a `loading.tsx` there would turn every unknown offer into
  a 200 soft 404. The root skeleton lives in `components/site/route-skeleton.tsx` and is re-exported by the
  admin, auth, dashboard and pricing segments.

## Error monitoring and addresses (FREEJ-31)

Goal: a visitor's email address never reaches logs, history or Sentry through a URL, so the privacy notice is true.

**Audit (code, FREEJ-31).** No component, link, redirect, server action, auth flow or test puts an address in a
URL. The newsletter band is a link to `/alerts`; `/alerts` ignores `?email=` (the e2e spec `alerts.spec.ts` loads
`/alerts?email=...` on purpose and asserts nothing is prefilled); subscribe, confirm and unsubscribe go through
server actions with the token or address in a body. Reset-password and verify-email read no query
parameter. Sign-in reads only `redirect`, which is a path on this site (never an address; `resolveSafeRedirect` in
`src/lib/auth/safe-redirect.ts`, FREEJ-41) and is not sent to any service. Sign-up reads only `plan`; the billing success handler reads `upgrade`. Firebase's
`sendPasswordResetEmail` and `sendEmailVerification` are called without action-code settings, so their links point
at Firebase's own handler, and the app has no `oobCode` or `continueUrl` page (`verifyEmailAction(oobCode)` exists
but nothing calls it).
Two GET forms do take free text and can therefore carry an address if someone types one: the public search box
(`/freebies?q=`) and the admin user search by name or email (`/admin/users?q=`). They are left as GET forms
(shareable, no-JS search); Sentry is scrubbed of the value (below), but hosting request logs and browser history keep
it. The privacy notice says so for the search box.

**Scrubber.** `src/lib/sentry/scrub-event.ts` (`scrubEvent`) is the `beforeSend` and `beforeSendTransaction` hook of
the client (`src/instrumentation-client.ts`), server (`sentry.server.config.ts`) and edge (`sentry.edge.config.ts`)
configs. It walks the whole event (request url and query string, headers such as `referer`, transaction and span
names, span data and attributes, breadcrumb `to`, `from` and `url`, `extra`, `contexts`) and removes:

| What | Why it is listed |
| --- | --- |
| `token` parameter (exact name) | one-use alert tokens in the confirm and unsubscribe links |
| any parameter whose name contains `email` or `e-mail` (`email`, `Email`, `newsletter_email`, `emailAddress`, `EMAIL%5B%5D`), in the query or after a `#` | the parameter FREEJ-24 removed; kept as defence in depth |
| any parameter whose value contains an email address (`?q=a%40b.com`) | the two search boxes above |
| any address-shaped token (`name@domain.tld`, plain, `%40` or `%2540`) in any string: path segments, userinfo, nameless queries, fragments, free text | defence for places an address could land that no parameter name catches |
| the `next-router-state-tree` header (whole) and cookie headers | the router state can embed the query as JSON |

Each is handled in plain, percent-encoded (also nested in another URL and encoded twice), JSON, escaped-JSON and
percent-encoded-JSON form, and for a query held as a string, an object or a list of name/value pairs. It is pure
(never mutates), bounded, and never throws: a cycle becomes `[Circular]`, a shared child is walked once, an object nested
more than 12 deep becomes `[Filtered]`, and an event of more than 100 000 objects is dropped (`null`, as is any event it
cannot walk; keep Sentry's `sendClientReports` on so the drop is counted). Sentry's own `normalizeDepth` is the first line of
defence. The address pass needs a dotted domain ending in a label that starts with a letter, so stack-frame
file names (`node_modules/@sentry/core/...`, `@radix-ui/react-dialog`), decorators (`@Injectable()`), `user@host` and
`pkg@8.1.0` are left alone. Parameters such as
`oobCode`, `apiKey` (Firebase's public key appears as `?key=` on `googleapis.com` fetch breadcrumbs), `e` and `mail`
are deliberately **not** listed: they do not appear in any URL of this app, and adding names that never occur only
hides real findings. Add a name to `SENSITIVE_NAME` in `scrub-event.ts` (and a test) when a flow starts using one.
Letters of any script count in an address, also percent-encoded (`victim@b%C3%BCcher.example`). The memo that makes a
shared child cheap ignores depth and ancestry, so it can only over-filter, never leak. **Known false positives** (lossy,
not leaky): `logo@2x.png`, `icon@3x.webp`, `app@latest.js` and `version@1.0.beta` become `[Filtered]`.
**Not covered** (the part of the address before the stop leaks): a local part with a character outside letters, digits
and `_.+!%-` (`o'brien@x.test`, `victim#1@x.test`, `victim&x@x.test`, `victim=x@x.test`, a quoted part with a space);
a local part longer than 256 characters (only its last 256 are replaced); other spellings of `@` (full-width, `&#64;`, a
literal `@`); an IP-literal domain (`victim@192.168.0.1`); percent-encoded letters in a parameter name
(`?%65mail=`, `?t%6Fken=`; bracket escapes such as `%5B` are handled).
Tests: `src/__tests__/unit/lib/sentry-scrub-event.test.ts` (token), `sentry-scrub-email.test.ts` (hostile URLs,
every shape above), `sentry-scrub-hardening.test.ts` (cycles, shared references, depth, fragments, encoded names,
address tokens, false-positive guards) and `sentry-configs.test.ts` (the three configs register the hooks). They run on constructed events; **nothing here has been checked against a live Sentry project**.

**Backend.** `app/core/sentry.py` already dropped query strings, bodies and cookies; FREEJ-31 also redacts an email
address from the request path and from every string in breadcrumb data, `contexts`, `extra` and headers (to 20 levels; a cycle or anything deeper becomes `[Filtered]`, tuples and sets come back as lists),
because the Brevo contact lookup and delete calls put
the subscriber's address in the path (`/contacts/<email>`) and the SDK's httpx integration records that URL.
`configure_logging` now holds the `httpx` and `httpcore` loggers at WARNING for the same reason (httpx logs each
request line, URL included, at INFO). The uvicorn access log still prints the request line, so keep running the
backend with `--no-access-log` (see [backend-catalogue.md](backend-catalogue.md#operations-notes)).

**Request-log retention (owner).** Hosting platform request logs record the full URL of every page load, so the
alert token of a confirm or unsubscribe link and the text of any search stay there for as long as the platform keeps
them. Check the retention setting and shorten it before launch; the privacy notice does not state a period.

## Next phases

- Phase 2 (FREEJ-17 to FREEJ-22): backend models, migrations, public read API, admin offers. FREEJ-21
  (this switch) is done; FREEJ-22 (admin UI) calls `revalidateCatalogue`.
- Phase 3 (FREEJ-23 to FREEJ-27): `/go/[offerId]` redirect, click events, email provider,
  sitemap, structured data.
- Phase 4 (FREEJ-28, FREEJ-29): favourites (FREEJ-28, done) and the remaining nice-to-haves.
