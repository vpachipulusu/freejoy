# Freejoy SEO (FREEJ-25, updated in FREEJ-21)

How the public site decides what search engines may index, what the sitemap and robots.txt contain,
and which structured data it emits. FREEJ-21 switched the catalogue from fixtures to the catalogue API;
the sections below describe the site as it is now. Code lives in `nextjs-frontend/src/lib/seo/`,
`src/components/seo/json-ld.tsx`, `src/app/sitemap.ts` and `src/app/robots.ts`. Page metadata is set in
the pages under `src/app/(public)/`.

## Indexing policy

Indexability is decided by where the catalogue comes from (`getCatalogueSource()`) and by each offer's
own data, not by a flag in the code. The one exception is `ALERTS_FORM_LIVE` (an environment variable read in
`src/app/(public)/alerts/page.tsx`, unset by default; the About page reads it too, through `src/lib/alerts/live.ts`): a deliberate pre-launch hold that keeps `/alerts` noindex
and shows a "not open yet" notice instead of the form, until `ALERTS_FORM_LIVE=true` is set (read when the
server starts) once an email provider is configured. The page has no catalogue data to decide from.

| Helper (`lib/seo/indexing.ts`) | Rule |
|---|---|
| `isIndexableOffer(offer, today)` | `source === 'api'`, not `demo`, `status === 'published'` and `isLive(offer, today)` |
| `isCatalogueApiBacked(source)` | `source === 'api'`, where `source` is `await getCatalogueSource()` (`CATALOGUE_SOURCE`, default `api`) |
| `isIndexableCategory(category, source)` | the category is enabled and the catalogue is API-backed |
| `isRefinedQuery(query)` | any search, category, type, postage, non-default sort, or page above 1 |
| `expiredIndexingPolicy(offer, today)` | see "Expired offers" |
| `endedOfferState(ended, today)` | `ended` or `gone` for the API's 410 stub, see "Expired offers" |

`lib/seo/metadata.ts` turns those answers into Next.js `robots` and `openGraph` values:

| Page | Fixture source | API source |
|---|---|---|
| `/` | `noindex` | `index, follow` |
| `/freebies` page 1, no filter | `noindex` | `index, follow` |
| `/freebies` with any filter, search, sort or page above 1 | `noindex, follow` | `noindex, follow` |
| `/category/[slug]` | `noindex` | `index, follow` |
| `/category/[slug]` with a filter, search, sort or page above 1 | `noindex, follow` | `noindex, follow` |
| `/freebies/[slug]`, live | `noindex` | `index, follow` |
| `/freebies/[slug]`, ended | `noindex, follow` | `noindex, follow` |
| any of the above, **while the page shows its outage state** | unchanged (`noindex`) | unchanged: the normal robots for that URL |
| `/alerts` | `noindex` | `noindex` until the form is live |
| `/saved` | `noindex, follow` | `noindex, follow` always: the list lives in the visitor's browser, so the page is the same empty shell for a crawler. Not in the sitemap; not disallowed in `robots.txt`, so the `noindex` can be read |
| `/partners` | `noindex` | `noindex` while it carries the draft banner (`NOINDEX_ROBOTS` in `src/app/(public)/partners/page.tsx`; drop the banner and switch to `INDEXABLE_ROBOTS` together). Always listed in the sitemap, with no `lastModified` |
| Legal pages | not part of this story | not part of this story |

Every refined view canonicalises to its base path (`/freebies`, `/category/<slug>`), so the filter
combinations never compete with the base page.

### How the flip to indexable works (FREEJ-21)

The facade (`src/lib/catalogue/index.ts`) reads from the API unless `CATALOGUE_SOURCE=fixture`:

1. `generateMetadata` on `/`, `/freebies` and `/category/[slug]` asks `getCatalogueSource()`. API source
   returns indexable robots for the unrefined view; fixture source returns `noindex`. **It does not matter
   how many offers are live**: a lull, or launch day with nothing published, leaves the listings
   indexable (the pages show their empty states).
2. Each live API offer page returns indexable robots through `offerRobots`; the offers carry
   `source: 'api'` and `demo: false`, so the `demo` badge and wording disappear.
3. `sitemap.xml` lists the enabled categories and the indexable offers.

Pages render per request (`dynamic = 'force-dynamic'`); the catalogue reads behind them are cached for a
minute (`unstable_cache`, tag `catalogue`), so a data change reaches search engines within a minute, and an
outage cannot freeze a page, because a failed read is thrown and never cached. See
[freejoy-frontend.md](freejoy-frontend.md#catalogue-api-wrapper-freej-21).

### When the catalogue cannot be read

- The page shows its outage state (`InlineError`, a retry button, curated copy) and answers **HTTP 200 for
  as long as the outage lasts**. It does **not** add `noindex`: `generateMetadata` does not read the
  catalogue for its robots, so the listing pages (`/`, `/freebies`) keep the robots they would have had
  (indexable in API mode, a refined view still `noindex, follow`, fixtures always `noindex`). A short
  outage therefore cannot de-index them. The two slug pages (`/freebies/[slug]`, `/category/[slug]`) cannot
  know their own record during an outage, so they answer `noindex, follow` with a generic title; they are
  re-crawled and re-evaluated once the backend is back. Dynamic responses are
  `Cache-Control: private, no-cache, no-store`, so nothing stores the error page, and the next request
  after the backend recovers renders normally.
- **Follow-up: answer 5xx during an outage.** A 200 with an error body is a soft error to crawlers; a 503
  would be more honest. A Next.js page cannot set its own status once it streams, so this needs a
  route handler or the proxy; it is not part of FREEJ-21.
- `generateMetadata` never throws for an outage. Only a `CatalogueUnavailableError` is folded into the
  error state; any other error is a bug and propagates.
- The sitemap logs the failure and falls back to the static pages only (`lib/seo/live-offers.ts`), so
  `/sitemap.xml` never throws because the catalogue is down. During an outage it therefore lists fewer
  URLs; search engines do not drop URLs for a short gap.

One consequence to know about: "API-backed" is a property of the whole catalogue, as specified for
FREEJ-25. A category with no live offers is therefore still indexable once the catalogue is API-backed. If
thin category pages become a problem, tighten `isIndexableCategory` to require an indexable offer in that
category.

`getToday()` returns the **Europe/London calendar date** (`YYYY-MM-DD`) for the API source, because
`isLive` and the 30-day expiry policy compare calendar days. Backend timestamps are converted to London
dates in `api-mapper.ts` (23:30 UTC on 1 July is 2 July in London), and the clock is read in
`api-source.ts` only; fixtures keep the fixed `FIXTURE_TODAY`.

## Expired offers

For a fixture offer, `expiredIndexingPolicy` returns one of four states. For an offer from the catalogue API
the catalogue itself decides: the API answers **410** for an offer that was public and has ended, the
facade turns that into `lookupOffer(slug) => { kind: 'ended', ended }` (title, category, London end date),
and `endedOfferState(ended, today)` applies the same 30-day rule. An unknown or never-published offer is a
404 (`not_found`); a slug that is not `^[a-z0-9-]+$` is a `not_found` without calling the API.

| State | When | Status | Robots |
|---|---|---|---|
| `live` | published and not past `endAt` | 200 | decided by the data (above) |
| `ended` | expired, or past `endAt`, for up to 30 days (`EXPIRED_NOINDEX_DAYS`); the API's 410 within 30 days of its `ended_at` | 200 with the "This offer has ended" state | `noindex, follow` |
| `gone` | more than 30 days after `endAt` / `ended_at` | 410 by policy, 404 as shipped | `noindex` |
| `unavailable` | draft or archived | 404 | n/a |

Edge cases, all pinned by tests:

- An expired offer with no `endAt`, or an `endAt` that is not a readable date (for example `soon`),
  has no date to count from, so it stays `ended`.
- An offer marked `expired` whose `endAt` is still in the future stays `ended`, and only becomes
  `gone` once `today` is more than 30 days after `endAt`.
- Day counting uses calendar dates in UTC arithmetic, so BST starting or ending and year boundaries
  do not shift the count.
- `OfferIndexingPolicy.httpStatus` is reserved for the Phase 3 410 ticket: no page reads it yet.

The API's 410 stub with no `ended_at` (an editor retired the offer without an end date) has nothing to count
from, so it stays `ended`.

**What actually ships for `gone`: a 404, not a 410.** A Next.js page cannot set its own status code;
`notFound()` always answers 404. A 410 needs a route handler or the proxy, and the proxy runs without
catalogue access. So for an API-sourced offer more than 30 days past its end date the page calls
`notFound()` (a real 404). Search engines treat a 404 and a 410 almost the same way; a 410 only drops the URL
a little faster. Making it a real 410 is a **Phase 3 follow-up** (for example a catalogue-aware route
handler or a rewrite for offers the API reports as gone). Fixture offers are never "gone" so the design
preview keeps rendering.

## Sitemap and robots.txt

`/sitemap.xml` (`src/app/sitemap.ts`, built by `lib/seo/sitemap.ts`) lists:

- `/`, `/freebies` and `/partners` (FREEJ-55, no `lastmod`), always;
- each enabled category, only when the catalogue is API-backed;
- each indexable offer (published, live, API-sourced, not demo), through the facade (`getLiveOffers()` walks
  the API 50 offers at a time, up to 2000, each page cached for a minute).

Expired, draft and fixture offers never appear. `lastModified` is the offer's own `lastVerifiedAt`
(or `startAt`) when present; `/`, `/freebies` and a category use the newest of their offers. Nothing
uses the wall clock, and an entry without a date simply has no `lastmod`.

The sitemap renders per request (`dynamic = 'force-dynamic'`), so it tracks the catalogue within the
cache minute and an outage is never baked into it.

**While `CATALOGUE_SOURCE=fixture` the sitemap lists only `/`, `/freebies` and `/partners`**, and all of those answer
`noindex`. `/partners` is `noindex` in every mode while it carries the draft banner. Search Console would flag "submitted URL marked noindex" if the sitemap were submitted from a
fixture-backed deployment. Submit it only from an API-backed one.

Sitemap URLs are built with each path segment percent-encoded, so a slug containing a space, `?` or
`#` cannot change the URL.

`/robots.txt` allows `/` and disallows `/admin`, `/dashboard`, `/settings`, `/profile`, `/security`,
`/go/`, `/api/`, `/login`, `/signup`, `/reset-password` and `/verify-email`, and points at `<site url>/sitemap.xml`. `/go/` is the affiliate redirect that Phase 3 adds.
`robots.txt` is not a security control and it does not remove already indexed URLs; the noindex meta
tags above do that job for public pages.

## Structured data (JSON-LD)

Emitted by `<JsonLd>` (`components/seo/json-ld.tsx`), a server component that serialises with every
`<` replaced by its JSON unicode escape (backslash, `u003c`) so page text such as `</script>` cannot
close the script tag; the U+2028 and U+2029 line separators are escaped the same way. It adds no
visible content.

| Page | Schemas |
|---|---|
| `/` | `WebSite` (with a `SearchAction` to `/freebies?q={search_term_string}`) and `Organization` |
| `/category/[slug]` | `BreadcrumbList` (Home, category) |
| `/freebies/[slug]` | `BreadcrumbList` (Home, category, offer) |

`Organization` has a name and URL only. A `logo` is added only when `buildOrganization` is given the
path of a real brand asset; the current `public/icon-*.png.svg` files are placeholders (a black tile
with the letter "S"), so none is declared.

### Why there is no `Offer` or `Product` schema

Google's `Offer` and `Product` rich results describe something the page sells, with a price,
availability and often ratings. Freejoy sells nothing: it links to other merchants' promotions, the
terms change without notice, and it holds no price, stock level or review that it could state
truthfully. Marking up a free sample as an `Offer` with `price: 0` would claim a transaction the site
cannot honour, and invented availability or ratings would be misleading structured data that can lead
to a manual action. The builders therefore never emit `Offer`, `AggregateOffer`, `Product`, `Review`
or `AggregateRating`, nor any `price`, `priceCurrency`, `availability` or `rating` key; a unit test
walks every builder's output to enforce that. Reconsider only with a Google-documented type that
fits (for example `Event` for a dated competition) and data the editors verify.

## Metadata details

- Canonical and Open Graph (`og:url`, title, description, `en_GB`, site name) are set per page
  through `openGraphFor`. A page-level `openGraph` replaces the root layout's, so the root
  `og-image.png` is not inherited (see the follow-ups: that file does not exist).
- The site origin comes from `NEXT_PUBLIC_APP_URL` (`lib/seo/urls.ts`, read at call time and
  shared by canonicals, the sitemap, robots.txt and JSON-LD). In production off Vercel, rendering
  throws if it is unset, and a value must be a valid https URL (a loopback host is allowed over
  http because CI and the local e2e build use `http://localhost:3000`). On Vercel with it unset the
  deployment URL is used; in development and test it falls back to `http://localhost:3000`. A
  trailing slash is stripped. The Open Graph title on `/freebies` is always "All freebies"; the
  `<title>` still follows the search text.
- Page-level `robots` replaces the root layout's, so indexable pages repeat the snippet and
  image-preview allowances (`INDEXABLE_ROBOTS`).

## Owner follow-ups

These need a human, a public URL or a decision, and were not done in FREEJ-25:

1. **Google Rich Results Test and Search Console.** The Rich Results Test needs a public URL. Once a
   real deployment has API data, run it on `/` (WebSite, Organization) and on one offer and one
   category page (BreadcrumbList), then submit `/sitemap.xml` and spot-check URL Inspection.
2. **Favicons and the share image are missing.** `src/app/layout.tsx` references `/favicon.ico`,
   `/favicon-16x16.png`, `/favicon-32x32.png`, `/apple-touch-icon.png` and `/og-image.png`, none of
   which exist in `public/` (the first three 404 in Lighthouse). Add real brand assets, then pass the
   logo path to `buildOrganization` and an image to `openGraphFor`.
3. **Real 410 for gone offers** (Phase 3): replace the 404 described under "Expired offers".
4. **Unknown `/category/[slug]` and `/freebies/[slug]` answer a real 404.** No `loading.tsx` may sit above the
   public pages: a loading boundary streams the shell before `notFound()` runs and turns every 404 into a
   200 soft 404. The root `loading.tsx` was moved into the admin, auth, dashboard and pricing segments
   (`RouteSkeleton`), and `src/__tests__/unit/config/loading-boundary.test.ts` fails if one returns.
5. **Re-run Lighthouse** now that the catalogue can be API-backed; the SEO score is capped by the deliberate
   noindex today. Numbers so far: `docs/evidence/phase3/lighthouse.md`.
6. Decide whether thin categories (no live offers) should stay indexable.
