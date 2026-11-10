# Freejoy affiliate redirect (`/go/[offerId]`)

Every public call to action on a real offer links to `/go/<offerId>` on Freejoy, never straight to the brand. The
route records one privacy-preserving click and redirects to the destination stored on the offer's affiliate link.
Code: `src/app/go/[offerId]/route.ts`, `src/lib/outbound/` (decision and checks), `src/lib/api/outbound.ts` (backend
call), `app/api/v1/catalogue_click.py`, `app/services/outbound_clicks.py`, `app/core/affiliate_hosts.py`. The
affiliate-disclosure copy and the compliance rules behind it are in
[`../compliance/uk-affiliate-checklist.md`](../compliance/uk-affiliate-checklist.md) (A1, A2, A5).

## The flow

```
visitor clicks <a href="/go/<id>" rel="sponsored nofollow noopener">
  -> proxy.ts                        rate limit (general policy), security headers
  -> GET /go/[offerId]               Next.js route handler (server only)
       validate the id; ignore every query parameter
       POST /v1/catalogue/offers/{id}/click   (X-Internal-Key, no session)
            offer public?  active link?  destination valid and on an allowed host?
            -> insert ONE click_events row, commit, return the destination
       re-check the destination is https; build the Location from a URL object
  <- 307  Location: <destination>   Cache-Control: no-store   Referrer-Policy: strict-origin-when-cross-origin
```

The browser never calls FastAPI. The destination is never in a page, a script or a query string: it exists only in
the `affiliate_links` row, the backend response to the Next.js server, and the `Location` header.

## Responses

| Outcome | `/go/<id>` answers | Backend answer | Click row |
|---|---|---|---|
| Live offer, usable link | `307` to the destination | `200 {"success": true, "data": {"destination_url": ...}}` | one |
| Offer that was public and has ended | `302` to the **relative** `/freebies/<slug>?ended=1` | `410` with `expired: true` and the slug (same body as `GET /v1/catalogue/offers/{slug}`) | none |
| Unknown, draft, archived, never-live, hidden category or inactive merchant | `404` | `404 Offer not found` (one identical body) | none |
| Live offer with no usable link (none active, bad URL, host not allowed) | `404` | the same `404 Offer not found` | none |
| Malformed id | `404`, backend not called | `422` if called | none |
| Rate limited | `429` with the backend's `Retry-After` (default 60 s, capped at 3600) | `429` | none |
| Backend down, key missing, bad response, or a destination the route refuses | `503` plain text, no backend text, `Retry-After: 30` | any other status | none |
| Not a top-level navigation (`Sec-Fetch-Mode` present and not `navigate`, or `Sec-Fetch-Dest` present and not `document`: an image, iframe, script or `fetch()`) | `204`, `no-store` | not called | none |
| Speculative load (`Sec-Purpose: prefetch`) | `204` | not called | none |
| `HEAD` | `405` | not called | none |

Every response is `Cache-Control: no-store`. "Public" is decided by `CatalogueService` (`get_offer_by_id`), the same
code and the same published, not-ended, enabled-category, active-merchant rule as the public catalogue API
([backend-catalogue.md](backend-catalogue.md)), so the redirect can never follow an offer the site would not show.
`/go/` is disallowed in `robots.txt` and not in the sitemap.

## Validation rules

Applied twice, in the backend and again in the route, so a bug in one layer is not an open redirect.

1. **Only the path id counts.** It must match `^[A-Za-z0-9_-]{1,64}$`. `?url=`, `?to=`, `?redirect=`, `//evil.com`
   and every other query parameter are never read. No header, cookie or body chooses the destination.
2. **The stored URL is re-validated** with `validate_https_url` (`app/core/catalogue_urls.py`): at most 2048 ASCII
   characters; no whitespace, control or format characters, backslash or percent-encoded control character (no CRLF
   header splitting); scheme exactly `https` (so `javascript:`, `data:`, `http:` and `//host` fail); an authority is
   required (`https:host` fails); no user info (`https://good.com@evil.com` fails); no IPv6 literal; port 443 only;
   a DNS name with at least two labels whose last label is not a number (no IPv4 in any spelling, no `localhost`).
3. **The host must be allowed for the link's provider** (next section). A link that fails is skipped; the oldest
   active link that passes is used; if none passes the answer is the 404.
4. **The route repeats the https check** (`parseHttpsDestination`): a clean absolute `https:` URL with a host, no
   user info, default port, none of the forbidden characters. The `Location` is built from the resulting `URL`
   object, never by string concatenation. It also mirrors the backend host rule: ASCII only (an IDN must be
   punycode, so a Cyrillic lookalike is refused), no `%` in the written host, at most 253 characters made of valid
   1 to 63 character labels, at least one dot, no trailing dot, no IPv6 literal, and a last label that is not a
   number. The ended-offer redirect uses a slug checked against the slug pattern and a
   **relative** `Location` (`/freebies/<slug>?ended=1`), so neither `Host` nor `X-Forwarded-Host` can put another
   origin into it.

The backend logs a link id and a reason code for a refused link, never the destination. The route logs the offer id
only. Neither logs query strings, headers or request bodies.

## What a click stores, and what it does not

`click_events` has exactly these columns (a test asserts it against the live schema):

| Column | Content |
|---|---|
| `id` | generated id |
| `offer_id` | the offer |
| `affiliate_link_id` | the link followed (set to null if the link is deleted) |
| `occurred_at` | the time **truncated to the minute** by the database default, so it cannot be joined to an access-log line |
| `referrer_host` | the host name of the `Referer` header only (for example `www.google.co.uk`); no path, query, port or credentials. Checked again in the backend: anything that is not a DNS name (or is an IP address) becomes null |
| `user_agent_class` | `DESKTOP`, `MOBILE`, `TABLET`, `BOT` or `OTHER`, computed in the route from the `User-Agent` header. The raw user agent is never sent to the backend |

**Not stored, anywhere:** IP address, cookie or device id, user id, session id, email, raw user agent, full referrer
URL, query string, destination URL. The client address reaches the backend only as `X-Client-IP` for the rate limit
(from `x-vercel-forwarded-for` on Vercel and nowhere else; see `getBackendClient`), and the rate limiter keeps a
counter, not a click. The backend request body accepts only `referrer_host` and `user_agent_class`
(`extra="forbid"`), so a caller cannot add more.

## No cookie

The route handler sets no cookie, reads no identifier from the device and writes no storage. The backend click
response sets none either. This is tested at three levels: the route unit test checks `Set-Cookie` is absent for
every outcome, the backend tests check the same, and the Playwright spec checks the browser's cookie jar is empty
after a click. A visitor's own cookies are not forwarded to the backend (`withSession: false`).

## Clicks are not conversions

A `click_events` row means "a visitor was sent to a provider through Freejoy". It is not a sale, a sign-up, a
claim, an approved commission or proof the visitor qualified. Bots, double clicks and visitors who abandon the
provider page are all stored as clicks, and nothing here deduplicates them (the admin merchant report leaves out rows
classed `BOT`, and only those: see [backend-admin-analytics.md](backend-admin-analytics.md#merchant-click-report)). The provider's network reports are the only
source for conversions and commission. Do not present click counts as "claimed" or "redeemed" numbers anywhere.

**Revenue comes from the network's reports, never from clicks.** `commands/import_awin_reports.py` reads Awin's own
transaction report and stores each transaction (status, commission and sale amounts, the click reference Awin reports)
in `network_report_rows`, matching it to an offer and affiliate link only through that reference. `/go/` emits no
click reference today, so most rows match through the reference stored in the Awin link itself (or stay unmatched);
nothing infers a sale from a click. See [backend-network-reports.md](backend-network-reports.md).

## Provider host allowlist

`app/core/affiliate_hosts.py` decides which hosts a link may point to, from the link's `provider` text (the admin
API only allows `^[a-z0-9][a-z0-9_-]{0,39}$`). A host matches an entry when it is that host or a subdomain of it
(`www.awin1.com` matches `awin1.com`; `awin1.com.evil.example` does not).

| Provider | Allowed hosts |
|---|---|
| `awin` | `awin1.com` |
| `cj` | `anrdoezrs.net`, `dpbolvw.net`, `jdoqocy.com`, `kqzyfj.com`, `tkqlhce.com` |
| `partnerize` | `prf.hn` |
| `rakuten` | `linksynergy.com` |
| `tradedoubler` | `tradedoubler.com` |
| `direct` | exactly the host of the **merchant's stored website**, or that host with `www.` added or removed. No other subdomain. A merchant with no website cannot have a `direct` link followed |
| `sample` | `example.com`, and only when `ENVIRONMENT` is `local`/`test` or Vercel is `preview`/`development` (what `commands.seed_catalogue` writes) |
| anything else | never followed |

The hosts above are the networks' tracking domains as commonly used; **confirm each against the network's own
documentation or your account before relying on it** (they are unverified here). Networks that use a vanity domain
per publisher (for example Impact) are deliberately not listed: add the exact domain you were given.

**`direct` trusts the merchant website.** The merchant's `website` is written by an admin through the admin API, so a
`direct` link is only as trustworthy as that field and the admin accounts that can edit it. Every create and update
of a merchant is audited (`admin.merchants.create` / `admin.merchants.update`) and the audit row holds the old and
new `website`, so a change can be traced. When a website changes while the merchant has active `direct` links, the
backend logs a warning with the merchant id and the link ids (never URLs). Treat `merchants.write` as a sensitive
permission.

**Add a provider:**

1. Get the exact tracking host(s) from the network (not a guess, not a wildcard for a shared domain).
2. Add an entry to `PROVIDER_HOSTS` in `app/core/affiliate_hosts.py` and a row to the table above. This is a code
   change, reviewed in a pull request, on purpose: the allowlist is the control that keeps `/go/` from being an open
   redirect if an admin account or a database row is ever wrong.
3. Add a test in `tests/core/test_affiliate_hosts.py` (the parametrised test covers every registered provider
   automatically) and, for a real link shape, a case in `tests/services/test_outbound_clicks.py`.
4. Create the affiliate link in the admin with `provider` set to the new key. The admin API checks the URL shape
   **and** the provider/host pair when a link is created or when its provider or destination changes: an unknown
   provider is a `422` (`unknown_provider`) and a host the provider may not use is a `422` (`destination_not_allowed`,
   "Destination host is not allowed for this provider"). Switching a link **off** does not re-check, so a link
   that predates a rule can still be deactivated; switching one **on** does, so a legacy link with a bad host
   cannot be revived (`422 destination_not_allowed`).

The redirect repeats the check every time a link is followed, because rows can change outside the admin API and a
merchant's website can change after a `direct` link was saved. A link that fails there is skipped (the offer's
`/go/` answers 404 and the backend logs `affiliate link <id> refused: host not allowed`).

## Rate limits

- `src/proxy.ts` rate limits every `/go/*` request with the general policy (200 per minute per client, in-memory).
  `/api/*` rules are unchanged. Buckets are kept **per scope** (`auth`, `api`, `general`), so `/go/` traffic cannot
  spend the auth bucket and an exhausted auth bucket does not block `/go/`. A refused request is `429`,
  `Cache-Control: no-store`, with the security headers.
- The client key is `x-vercel-forwarded-for` on Vercel. **Off Vercel there is no trusted proxy header, so every
  client shares one `unknown` bucket per scope** (all `/go/` traffic together counts against 200 per minute). That
  is fine for local runs and previews behind a single caller; do not rely on this limiter alone behind another host.
- The backend limits `POST .../click` twice:
  - `OUTBOUND_CLICK_POLICY`, 60 per minute per forwarded client IP. Over it the answer is `429` with `Retry-After`.
  - `OUTBOUND_OFFER_POLICY`, 600 per minute per offer id (`app/core/rate_limit.py`). Over it the visitor is **still
    redirected** (the click endpoint answers `200` with the destination) but **no click row is written**.
    **Trade-off:** click counts are capped at 600 per offer per minute, so a crowd of distinct clients cannot inflate
    one offer's count faster than that, and a genuine surge is undercounted rather than turned into refused visitors.
    The redirect itself is never refused because of this cap. A cap-spent request still goes through every other
    check (public offer, usable link, allowed host).
  - Both fail open like the other public catalogue routes (a limiter outage records the click as usual).
- The proxy keys IPv6 clients by their full address, while the backend groups them by /64. This pre-existing gap
  is not specific to `/go/`: one IPv6 subscriber can spread requests over many addresses to get more proxy
  buckets, though the backend limits above still apply.

## What to tell a retailer about clicks

The admin report (`/admin/analytics`, "Clicks by merchant", with a CSV download) is the evidence for a retailer listed
directly. Send the CSV and say plainly:

- A click is one visitor sent from Freejoy to your page. It is **not** a sale, a sign-up or a claim, and it is not a
  count of different people: someone who clicked twice counts twice.
- We leave out clicks from visitors identified as automated (bots). We filter nothing else, so some automated or
  accidental traffic can remain.
- Days are UTC days. We record the time of a click to the minute and keep **no** visitor identity (no IP address, no
  cookie, no account), so we cannot say who clicked or how many were new.
- We keep click records for 13 months. A report that starts earlier than that is missing its oldest days.
- Your own figures (orders, visits from our referrer) are the only measure of what the clicks became; we never claim
  sales from clicks or call them redemptions.
- Your live listings are in the file (up to 100 offers, busiest first), with 0 where one had no clicks.

## Retention

Click rows are kept for **13 months (395 days)** and then deleted by `commands/prune_click_events.py`:

```bash
uv run python -m commands.prune_click_events            # default --days 395
uv run python -m commands.prune_click_events --days 90  # a shorter period; 0 or less is refused
```

It deletes rows whose `occurred_at` is older than the period, in committed batches of `--batch-size` rows (default
10000; the ids come from a limited subquery served by `ix_click_events_occurred_at`, migration `0003`), and prints
the total. If a batch fails it prints the error type and **how many rows were already deleted** to stderr and exits
1; the earlier batches stay deleted and a rerun carries on. It is safe to repeat. The period is
stated on the privacy page ("If you use an offer button"): change both together.

**Schedule.** `.github/workflows/scheduled-housekeeping.yml` runs it daily at 03:17 UTC (with the default `--days`;
after the offer health check, as its own step), using the `HOUSEKEEPING_DATABASE_URL` secret of the `housekeeping` GitHub Environment. A day with
nothing older than the period prints `deleted 0 click events`. It needs `SELECT` and `DELETE` on `click_events`
only. Setup, running it by hand and what a failure looks like:
[backend-offer-health.md](backend-offer-health.md#scheduling) and
[workflow.md](workflow.md#owner-setup-scheduled-housekeeping).

## Referrer policy

The redirect sends `Referrer-Policy: strict-origin-when-cross-origin`. The destination learns only the Freejoy
**origin** (never a path or query), which affiliate networks use to verify the referring domain; `no-referrer`
would leave clicks unattributed. This is a deliberate decision, recorded in the compliance checklist (A5.3).

## Running and testing it locally

Unit tests: `npm run test:unit` (route, wrapper, validation, CTA, proxy) and `uv run pytest` in the backend. The
`*_e2e.py` tests (`tests/api/test_catalogue_click_e2e.py`) need `TEST_DATABASE_URL`: they prove one row per
successful click, no row for any refusal, the column set, hostile stored destinations and the allowlist.

Playwright (`src/__tests__/e2e/go-redirect.spec.ts`) needs a production build, a running backend with seeded data and
the offer ids, so it skips unless they are set. The slug is optional: with it the spec also clicks through from the
real offer page (the frontend must use the catalogue API source, which is the default):

```bash
# backend (fastapi_backend/), against a local *_test or dev database you own
uv run alembic upgrade head && uv run python -m commands.seed_catalogue
uv run uvicorn app.main:app --port 8172 --no-access-log        # ENVIRONMENT=local, BACKEND_INTERNAL_KEY set
# ids: a live offer, an ended one (status EXPIRED), a draft
psql ... -c "select id, slug, status from offers where slug in ('coffee-discovery-pack','laundry-sheets-sample','cat-pouch-tasting-sample')"

# frontend (nextjs-frontend/)
npm run build && BACKEND_API_URL=http://127.0.0.1:8172 BACKEND_INTERNAL_KEY=<same key> npx next start -p 3190
GO_E2E_LIVE_OFFER_ID=<id> GO_E2E_LIVE_OFFER_SLUG=<slug> GO_E2E_ENDED_OFFER_ID=<id> GO_E2E_DRAFT_OFFER_ID=<id> \
  E2E_BASE_URL=http://localhost:3190 npm run test:e2e:go
```

`test:e2e:go` uses `playwright.go.config.ts`: chromium only, no login project and no web server.
`npm run test:e2e:local -- src/__tests__/e2e/go-redirect.spec.ts` also works if the ids are exported, but it runs the auth
setup project first and therefore needs `.env.e2e.local` ([local-e2e.md](local-e2e.md)).

## Known limits and follow-ups

- Fixture (demo) offers keep the "Demo offer" dialog: their ids have no row, so `/go/` would answer 404. The CTA
  switches to the link when a page passes `offerId` (`OfferHero` does so for `demo: false` offers).
- The "Ad" label and the sentence "Freejoy is paid a commission by the brand if you take up this offer through this
  link." are from the compliance checklist and still need the reviewer's sign-off on wording. Offer cards do not
  carry the label yet (checklist A1.3).
- Each valid request writes a row; nothing deduplicates repeated clicks, so a visitor who clicks twice counts twice.
  The per-client and per-offer limits only bound the rate.
- `click_events.referrer_host` comes from the visitor's browser and is untrusted
  (see [platform-models.md](platform-models.md)).
- `prune_click_events` is scheduled daily by the housekeeping workflow (see Retention above); a skipped or disabled
  schedule (on a public repository GitHub pauses cron after 60 days without activity) only delays deletion.
- A destination that no longer answers is not detected on the click path (the visitor gets the provider's error page).
  `commands.check_offer_health` reports dead offer source URLs and `direct` links, but it never requests a network
  provider's tracking link (that would record a click), so a dead `awin`, `cj` or other network link is only visible in
  the network's own reports. See [backend-offer-health.md](backend-offer-health.md); the housekeeping workflow runs it
  daily.
