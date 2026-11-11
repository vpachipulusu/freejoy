# Public catalogue API

Internal-key-only read endpoints that let the Next.js server render real published offers without a visitor
session. Code: `app/api/v1/catalogue.py` (routes), `app/api/v1/catalogue_schemas.py` (public response models),
`app/services/catalogue.py` (`CatalogueService`), `app/repositories/catalogue.py` (`CatalogueRepository`). Tables:
[platform-models.md](platform-models.md).

## Contract

| | |
|---|---|
| Auth | `X-Internal-Key` only (the browser never calls FastAPI). No user session; 401 without the key. |
| Rate limit | `PUBLIC_CATALOGUE_POLICY`: 120 requests per 60 s per client IP (`X-Client-IP`). Fails open; 429 with `Retry-After`. |
| Envelope | `{"success": true, "data": ...}` on success, `{"success": false, "error": "..."}` on error (422 is `"Invalid request"`). |
| Cache | `Cache-Control: private, max-age=60` plus a content-hash `ETag` on list, detail and categories (also on the 410). 304 with an empty body when `If-None-Match` matches. 401, 404, 422, 429 and 500 carry no cache header. |

### `GET /v1/catalogue/offers`

Published, non-expired offers with the total before paging.

| Parameter | Rule |
|---|---|
| `q` | Up to 100 characters. Case-insensitive match on title, summary or merchant name. `%`, `_` and `\` are matched literally (escaped before the `ILIKE`). Blank is ignored. |
| `category` | Category slug, up to 100 characters. An unknown or disabled category gives an empty list, not an error. |
| `type` | `free_sample`, `free_trial`, `cashback`, `voucher`, `competition`. May repeat (any of). Anything else is 422. |
| `postage` | `free` keeps only offers with no postage. Anything else is 422. |
| `featured` | `true` only featured, `false` only the rest, absent both. |
| `sort` | `newest` (default: start date, falling back to creation, newest first), `ending_soon` (soonest end date first, open-ended last), `a_z` (title). Ties break on the id, so paging is stable. |
| `limit` | 1 to 50, default 12. |
| `offset` | 0 to 10000, default 0. An offset past the end gives an empty `items` and the real `total`. |

```json
{"success": true, "data": {"items": [ <offer> ], "total": 31, "limit": 12, "offset": 0}}
```

### `GET /v1/catalogue/offers/{slug}`

- **200** the offer (`{"success": true, "data": <offer>}`).
- **404** `{"success": false, "error": "Offer not found", "code": "offer_not_found"}` (the code is what the Next.js app
  prunes a visitor's saved offer on; a route-level 404 has no code and never prunes) for an unknown slug **and** for a `DRAFT` or `ARCHIVED`
  offer, with an identical body, so a visitor cannot probe for unpublished content.
- **410 Gone** only for an offer that provably was public and has ended: `PUBLISHED` with `end_at <= now`, or `EXPIRED`
  with a `last_verified_at` (see the decision below). A 410 is never answered with 304.

### `GET /v1/catalogue/categories`

Enabled categories in display order (`sort_order`, then name), each with `offer_count`, the number of live offers
(the same rule as the list, so a count always matches what `GET /offers?category=` returns). A category with no live
offers is returned with `0`.

## Decision: how an expired offer is answered

The detail route answers **HTTP 410 Gone** with the error envelope extended by `expired: true` and a small stub:

```json
{
  "success": false,
  "error": "This offer has ended",
  "expired": true,
  "data": {"slug": "...", "title": "...", "category": {"slug": "...", "name": "..."}, "ended_at": "2026-05-31T23:59:00Z"}
}
```

Why a real 410 and not a 200 flag: a crawler and a CDN learn that the page is gone for good, and the frontend treats it
as a distinct, typed outcome (the generated client types the 410 body as `ReadOfferErrors[410]`). Why a stub and not 404:
an ended offer page is still useful ("this offer has ended, see similar offers in Category"), and it was public before.
The stub carries only the slug, title, category and end date, never the offer details (eligibility, steps, terms), so
an expired offer stops advertising its terms. `ended_at` is `null` for an offer an editor marked `EXPIRED` with no end
date, and also when its `end_at` is still in the future (an editor retired it early): `ended_at` is never later than now.
The 410 carries the same `Cache-Control` and `ETag` as the 200, but a matching `If-None-Match` still gets the 410 and its
body, never a 304.

**Which offers get a 410.** Only one that provably was public: status `PUBLISHED` with `end_at <= now`, or status
`EXPIRED` **with** `last_verified_at` set (the database CHECK guarantees every `PUBLISHED` row has one, so a verification
date on an `EXPIRED` row shows it once went live). Everything else (`DRAFT`, `ARCHIVED`, `EXPIRED` without
`last_verified_at`, unknown slug) gets the identical 404 body, so the 410 cannot be used to learn that a never-published
offer exists or what it was called. `last_verified_at` is read for this decision only; the 410 stub never contains it.

## The expiry rule

`CatalogueService.is_live`: `status == PUBLISHED and (end_at is null or end_at > now)`. `now` comes from the service
clock (UTC), is passed to the repository, and the same rule is applied in SQL by `live_conditions`; the e2e tests prove
both agree, including the boundary: an offer whose `end_at` equals `now` is already ended (strict `>`), one second later
is live (`test_an_offer_ending_exactly_now_is_expired_and_one_second_later_is_live`, which fixes the clock). `start_at` is not a filter: an editor who publishes an offer with a future start date has chosen to show it.
`DRAFT`, `ARCHIVED` and (for list and counts) `EXPIRED` offers never appear in lists or counts.

## Visibility beyond status

An offer whose **category is disabled** or whose **merchant is inactive** is hidden everywhere (list, detail 404,
category counts), because the public pages name both and a disabled category is not browsable. Enable the category or
reactivate the merchant to show its offers again.

## What is public

The offer shape is the allowlist `OfferData` in `app/api/v1/catalogue_schemas.py`: `id` (needed for the later `/go/[id]`
redirect), `slug`, `title`, `summary`, `description` paragraphs, `category {slug, name}`, `merchant {slug, name,
logo_url}`, `offer_type`, `sponsored`, `featured`, `image {tint, label, alt}`, `postage {kind, amount_minor, currency}`,
`requires_card`, `subscription_terms`, `eligibility`, `availability`, `start_at`, `end_at`, `last_verified_at`,
`freshness`, `steps`. Enum values are lower-case, as in `nextjs-frontend/src/lib/catalogue/types.ts`; the database
stores the upper-case names.

`sponsored` is `true` only when the offer's commercial basis is `SPONSORED` (the merchant pays or gives a benefit for
the listing); the frontend shows a "Sponsored" label for it. It is on the list items and the detail alike. The
three-way `commercial_basis` (`EDITORIAL`, `AFFILIATE`, `SPONSORED`) is **never** in a public response: the public
record carries it only to compute this flag, and a test checks no response contains the name or the values. A
featured offer can be sponsored; nothing lets `sponsored` be hidden. No sort or filter of the public list reads the
basis (`tests/repositories/test_catalogue.py` compiles them and checks), so sponsorship does not change the order of results.

`earns_commission` is on the **detail** body only (`OfferDetailData` extends `OfferData`; the list items do not carry it, because
a card links to the offer page and never to the provider). It is `true` only when the link `/go/<offerId>` would follow
right now is on an affiliate network that pays commission (its provider is not `direct` and not the sample provider); it is
`false` when that link is `direct` or the offer has no usable link. It is computed with the same function the outbound
click uses to choose its link (`app/core/followed_link.py`), so the page cannot describe a different link from the one a
visitor is sent to. The frontend shows the "Ad" label and the commission sentence only when it is `true`. The provider,
the link id and the destination are never in the response; see
[freejoy-affiliate.md](freejoy-affiliate.md#when-the-page-says-ad) (including the deploy order: backend, then frontend).

Never exposed: `publisher_notes`, the three-way `commercial_basis`, `status`, category and merchant ids, `source_url`, merchant `website`, timestamps other than the ones
above, and anything from `affiliate_links` (destinations are resolved server-side by the outbound click endpoint, see [freejoy-affiliate.md](freejoy-affiliate.md)). The
repository selects an explicit column list, so those columns never leave the database layer, and the response models
are a second, independent allowlist. To expose another field, add it to `OfferRecord`, the select list, `OfferData` and
the allowlist test together.

## Tests

- `tests/services/test_catalogue.py`: the expiry rule matrix and what each status becomes.
- `tests/repositories/test_catalogue.py`: the SQL built through `FakeSessionProvider`: bound parameters, `ILIKE`
  escaping, limit clamping, sort orders, live and visibility conditions, no editorial columns selected.
- `tests/api/test_catalogue.py`: routes over a fake repository: envelope, empty list, 404, 410, 422 for every invalid
  parameter, ETag/304, 401, 429, rate-limit key, 500, and the privacy allowlist tests.
- `tests/api/test_catalogue_e2e.py`: real Postgres (`TEST_DATABASE_URL`, see [testing.md](testing.md)): visibility,
  every filter, sorts, paging, search escaping, detail 200/404/410, category counts, and that notes and links never
  appear in a response.

## Consumers

The Next.js wrapper is `nextjs-frontend/src/lib/api/catalogue.ts` (FREEJ-21; see
[freejoy-frontend.md](freejoy-frontend.md#catalogue-api-wrapper-freej-21)). Call the generated SDK (`listOffers`,
`readOffer`, `listCategories`) only through `src/lib/api/` and `callBackend`. The 120/min limit is keyed by the
forwarded client IP, so the wrapper forwards the visitor's address in `X-Client-IP`, taken only from Vercel's
`x-vercel-forwarded-for` (`readTrustedClientIp()` in `src/lib/api/server.ts`), never from a header the client can
set, and omits it when that header is absent. Renders that forward nothing share one bucket keyed on the Next.js
server's egress IP, which the limit can exhaust; the wrapper's 60 second cache keeps that traffic low, and a search
with text bypasses the cache so it is limited per visitor. The wrapper maps 404 to "not found", 410 to the ended
page (it reads the stub's `data`), and every other failure, including 429, to an outage error that is never cached.

## Operations notes

- **Access logs.** `q` is a visitor's search text and travels in the query string. The uvicorn access log prints the
  request line including `?q=`, so run the backend with `--no-access-log` or put a scrubbing formatter on `uvicorn.access`.
- **Follow-up (later migration).** Search is `ILIKE '%term%'` on `lower(title)`, `lower(summary)` and `merchants.name`,
  which scans the live rows. That is fine at launch volumes; when the catalogue grows, add a `pg_trgm` GIN index on
  those three expressions in a new Alembic revision (the extension must be allowed on the managed Postgres).
