# Awin offers importer (`commands.import_awin_offers`)

One command that reads the promotions and vouchers of the Awin programmes you have **joined** (UK only) and turns
them into **DRAFT** offers for an editor to review. It is backend only: no route, no UI, no OpenAPI change.

```bash
# fastapi_backend/, with AWIN_PUBLISHER_ID and AWIN_OAUTH_API_TOKEN set (and DATABASE_URL unless --dry-run)
uv run python -m commands.import_awin_offers --dry-run      # fetch and validate, write nothing
uv run python -m commands.import_awin_offers                # import
uv run python -m commands.import_awin_offers --limit 100 --max-pages 2 --updated-since 2026-11-01
```

Code: `commands/import_awin_offers.py` (arguments, output, exit codes, Sentry), `app/services/awin_import_run.py` (the
paging loop), `app/services/awin_offer_import.py` (create, refresh, edit protection, audit),
`app/services/awin_promotions.py` (one item as untrusted input), `app/services/awin_offer_mapping.py` (item to rows,
slugs, the edit fingerprint), `app/repositories/awin_import.py` (SQL), `app/core/awin_client.py` (the HTTP client),
`app/core/call_limiter.py` (the 20-per-minute limiter), `app/core/plain_text.py` (HTML and control-character
removal), `app/models/platform/catalogue_import.py` and migration `0004` (provenance tables).

> **Not verified against the live Awin API.** Everything here was built and tested against fakes
> (`tests/fakes_awin.py`, `httpx.MockTransport`); no request has ever been sent to Awin and the code never uses a real
> token. Awin's documentation ([promotions](https://help.awin.com/apidocs/promotions), read 2026-11-09) does not say
> several things the parser needs; the [assumptions](#unverified-assumptions-the-first-dry-run-confirms-or-breaks)
> below are guesses, written so that a wrong guess fails with a reason code instead of importing something odd. The
> owner's first `--dry-run` with the real token is what confirms them.

## What it never does

- **It never publishes, verifies or features anything.** Every offer is created `DRAFT`, `featured = false`,
  `last_verified_at = null`, no freshness label. Nothing in the importer calls a lifecycle action.
- **An imported draft cannot be published until an editor acts.** It sits in a disabled category named "Imported
  (uncategorised)" on an inactive merchant and is unverified, so `publish_blockers` returns `category_disabled`,
  `merchant_inactive` and `not_verified` until an editor picks a real category (enabled), activates the merchant and
  verifies the offer. A test drives the real admin publish path and asserts exactly those three.
- **It never overwrites an editor's work** (see [edit protection](#edit-protection)).
- **It never requests anything but the promotions endpoint**, never a tracking link or a merchant site.
- **It never prints, logs or sends** a URL, the token, a voucher code or advertiser copy. Skipped items are logged as
  a promotion id (an integer) and a reason code.

## Settings

| Variable | Notes |
| --- | --- |
| `AWIN_PUBLISHER_ID` | Positive integer. Optional for the app (a blank value means unset). |
| `AWIN_OAUTH_API_TOKEN` | `SecretStr`, trimmed; a value with whitespace inside stops startup (without echoing it). Optional for the app. |

Both are in `fastapi_backend/.env.example` with no values. The command exits **2** naming the missing variable when
either is empty (a `--dry-run` also needs them: it calls the API). The token is **only** ever sent in an
`Authorization: Bearer` header. Awin also accepts an `accessToken` query parameter; it is never used. The API host
(`https://api.awin.com`) is a constant, not a setting.

## Flags and exit codes

| Flag | Default | Meaning |
| --- | --- | --- |
| `--dry-run` | off | Fetch and validate, write nothing, open no database connection, print counts. |
| `--limit N` | 500 (1 to 5000) | Most promotions read in one run (valid or not). The page size follows it, within Awin's 10 to 200. |
| `--max-pages N` | 10 (1 to 50) | Most pages fetched. Awin allows 20 calls a minute, so 50 pages take at least 2.5 minutes. |
| `--updated-since YYYY-MM-DD` | unset | Only promotions Awin updated since that date (exactly that shape). |

| Exit | Meaning |
| --- | --- |
| `0` | The run completed. Skipped items and a stop at a limit do **not** change this. |
| `1` | The run failed. stderr names the stage and a reason code or error **type** (never text) and how much had been committed; those batches stay committed and a rerun carries on. One Sentry event is sent (below). |
| `2` | Bad arguments (out of range, unknown flag, a malformed date) or missing `AWIN_*` settings. |

stdout is counts only:

```
import-awin-offers: fetched 45 promotion(s) in 1 page(s); 41 valid, 4 skipped.
import-awin-offers: created 30, updated 5, unchanged 5, source changed and not applied 1, skipped 4.
import-awin-offers: skipped by reason: not_joined 2, tracking_host_not_allowed 2.
```

Skip reasons (`SkipReason`): `invalid_item`, `invalid_id`, `unsupported_type`, `invalid_advertiser`, `not_joined`,
`invalid_title`, `invalid_text`, `field_too_long`, `invalid_date`, `invalid_window`, `ended`, `invalid_url`,
`source_url_on_tracking_host`, `missing_tracking_url`, `invalid_tracking_url`, `tracking_host_not_allowed`,
`region_mismatch`, `invalid_voucher`, `duplicate_in_feed`, `slug_unavailable`.

## How a run works

1. Request page 1 (`POST /publisher/{id}/promotions`, filters `membership=joined`, `type=all`, `status=active`,
   `regionCodes=["GB"]`, optional `updatedSince`, `pagination {page, pageSize}`).
2. Treat every item as untrusted (`parse_promotion`, below), drop duplicates, then write the valid ones in
   **batches of 50**, each in its own database transaction that commits once. A failure stops the run; earlier batches
   stay. A rerun is idempotent, so it carries on.
3. Stop when a page holds fewer items than asked for (an empty page included), or the response said how many pages
   exist and this was the last, or `--limit` items were read (`stopped at --limit`), or `--max-pages` pages were
   fetched while the last was full (`stopped at --max-pages`). A page whose promotion ids were all seen on earlier
   pages is not trusted: the run fails with `pagination_repeat`.

Concurrent runs take turns: each batch starts with `pg_advisory_xact_lock`, so two runs can never both create the same
offer (tested with two simultaneous runs). Nothing schedules the command; run it by hand or add a schedule (a daily
run would do). It needs outbound HTTPS to `api.awin.com`, `DATABASE_URL`, and optionally `SENTRY_DSN`.

## Validation of each item

`parse_promotion` returns a `Promotion` or a `Rejected(reason)`; a rejection never carries the item.

- **Types:** ids are positive integers (not strings or booleans), `type` is `promotion` or `voucher`,
  `advertiser.joined` must be the boolean `true` (`false` is `not_joined`, anything else `invalid_advertiser`).
- **Text** (`app/core/plain_text.py`): tags removed with `html.parser` (re-run until nothing tag-like is left, so
  `&lt;b&gt;` cannot come back as markup; `script` and `style` content dropped), entities decoded once, NFC, control
  characters and zero-width or bidirectional formatting characters dropped, whitespace collapsed. Lengths are the admin
  API's (title 200, summary 300, paragraph 2000 and at most 20 of them, terms 500): longer text is cut on a word with
  an ellipsis, text over 20,000 characters rejects the item (`field_too_long`), a title or advertiser name that is empty
  after cleaning rejects it. Only plain text is stored.
- **Dates:** `YYYY-MM-DDTHH:MM:SS[.fraction][Z]` read as UTC and nothing else (no offset, no date-only, years 2000 to
  2100). An end not after the start is `invalid_window`; an end already passed is `ended`. Missing or empty is open.
- **URLs:** `url` (optional) and `urlTracking` (required) go through `validate_https_url`. `urlTracking` must also be on
  an Awin host via `affiliate_hosts` (`awin1.com` and subdomains; `awin1.com.evil.example`, `evilawin1.com`, other
  networks' hosts and user-info or port tricks are refused). `url` must not be on any network's tracking host.
- **Regions:** the request asks for GB; an item that says it is for somewhere else is `region_mismatch`.

## What is written

| Thing | Value |
| --- | --- |
| Offer status, `featured`, `last_verified_at`, `freshness` | `DRAFT`, `false`, `null`, `null` |
| `offer_type` | `VOUCHER` for both `voucher` and `promotion` (the closest existing type). Never `FREE_SAMPLE`; an editor changes it if the offer is something else. |
| Postage, card | `postage_kind = CHECK_TERMS`, no amount; `requires_card = false`. Nothing is claimed that the feed does not say. |
| Title, summary, description | The cleaned title; the first paragraph of the description (or the title); the description paragraphs. |
| `eligibility` | The promotion's terms, or "See the offer terms on the merchant's site before you claim." |
| `availability` | A fixed "Listed for UK customers. Check the merchant's terms for dates and exclusions." |
| Image | Placeholder: tint `cream`, label = the advertiser name (40 characters), fixed alt text. |
| `start_at`, `end_at` | The parsed dates (see the assumption about the end date). |
| `source_url` | The promotion's own `url`. |
| `publisher_notes` (internal, never serialised) | "Imported from Awin (promotion N). Review every field before publishing." |
| Affiliate link | One `awin` link, `destination_url` = `urlTracking`, active. Created through the same host rules the admin API applies. |
| Slug | The title slug plus `-<promotionId>` (for example `10-off-everything-1001`), else `awin-<id>`. Generated once and never changed, so it is stable for one promotion. A slug taken by a person's offer falls back to `awin-<id>`; if that is taken too the item is skipped (`slug_unavailable`). |

**Voucher codes are not stored.** The offer model has no field for one, and putting a code in free text would hide it
from review. A voucher with a code only gets a line in `publisher_notes` saying one exists at Awin. The editor adds the
code by hand. A change of code alone is therefore not seen as a change at the source.

### Category and merchant

- **Category.** `offers.category_id` is `NOT NULL` and the constraint is untouched. The importer creates (once, at
  run time, audited) the category "Imported (uncategorised)", slug `imported-uncategorised`, `enabled = false`, and puts
  every draft in it. It is disabled, so a draft in it cannot be published (`category_disabled`) and it is not shown on
  the site. If it is ever found enabled, the importer refuses to run (`import_category_enabled`) rather than create
  offers that could be published into a public category. It is created by the command rather than by the migration so
  that the downgrade needs no data handling.
- **Merchant.** `merchant_imports` maps an Awin advertiser id to a merchant (by id, never by name). A new advertiser
  gets a new merchant: `active = false` (so `merchant_inactive` blocks publishing), the cleaned advertiser name, no logo,
  and a website only when the promotion's own `url` gives one (the origin, for example `https://www.acme.example`, never a
  network tracking host). The slug is the name, else name plus advertiser id, else `awin-advertiser-<id>`. An existing
  merchant with the same name is **never reused or touched**. Merchants are created once and never refreshed or
  activated by a later run.

## Provenance model

Two new tables (migration `0004`, models in `app/models/platform/catalogue_import.py`), chosen over nullable columns on
`offers` and `merchants` so the catalogue tables are untouched and the importer's bookkeeping can change on its own:

- `offer_imports`: `provider`, `external_id` (the promotion id), `offer_id` (FK, cascade, unique), `source_hash`,
  `applied_source_hash`, `applied_content_hash`, `applied_at`, `source_changed_at`. Unique `(provider, external_id)`.
  CHECKs: provider matches `^[a-z][a-z0-9_]{0,39}$`, external id 1 to 64 characters, each hash 64 characters.
- `merchant_imports`: `provider`, `external_id` (the advertiser id), `merchant_id` (FK, cascade, unique). Unique
  `(provider, external_id)`.

The downgrade drops both tables (offers and merchants stay; a later import would recreate the promotions as new
drafts), so run it only on a throwaway database or before any import.

## Edit protection

An offer's row in `offer_imports` says whether the offer is still an untouched import:

- `source_hash` is the latest source content seen (the **normalised** promotion, hashed, so an unused key appearing in
  the response never counts as a change). `applied_source_hash` is the source content the offer currently reflects.
- `applied_content_hash` is a **fingerprint of the offer and its links as the importer left them**: every field an
  editor can change (`EDITABLE_FIELDS`), plus `status` and `last_verified_at`, plus each link's provider, destination,
  tracking id and active flag (not ids, slugs or timestamps).

On each item the importer compares hashes, in this order:

| Situation | Result |
| --- | --- |
| No provenance row | Create (`created`). |
| `source_hash` equals the new hash | Nothing is written (`unchanged`). A second run changes nothing, not even `updated_at`. |
| Source changed, the offer is `DRAFT` **and** has exactly one link **and** its fingerprint equals `applied_content_hash` | Refresh the imported fields and the link, re-store both hashes (`updated`). |
| Source changed, anything else (edited, published, expired, archived) | **Content is never written.** `source_hash` is updated and `source_changed_at` is set (`source_changed`, audit `admin.offers.source_changed`), so a later review queue can list `WHERE source_changed_at IS NOT NULL`. If the source returns to what the offer reflects, the marker is cleared. |

**Why a fingerprint and not `updated_at`.** `updated_at` also moves for the importer's own writes and for housekeeping
that is not an edit (expiry, `last_verified_at` bookkeeping), so comparing it would either miss edits or treat every
refresh as an edit. The fingerprint changes exactly when a person changes what the offer says or where its links go.
Editing any field, adding or deactivating a link, choosing a category, verifying the offer or changing its status
all end the import's right to overwrite it. (Choosing a category counts: an editor who has done that has started
reviewing it.) A test per editable field proves it, an e2e test edits through the real admin service, and another holds
the offer's row lock as an editor would: the importer waits for it and then judges the saved offer.

## Audit

Every create, refresh and source-change marker writes an `audit_logs` row through
`AuditService.record_system_action` (the path `check_offer_health` uses): `user_id` is null and `metadata.actor` is
`commands.import_awin_offers`. Actions reuse the admin vocabulary: `admin.categories.create`,
`admin.merchants.create`, `admin.offers.create`, `admin.offer_links.create`, `admin.offers.update` (with
`details.reason = source_refreshed` and the field names, never the text), `admin.offer_links.update`, and
`admin.offers.source_changed`. Rows hold ids, enums, slugs, counts and a link's destination **host**; never copy, a full
URL, a code or a token (a test greps the stored rows).

## Reporting and Sentry

A failed run (exit 1) sends **one** event, `Awin offers import run failed`, level `error`, context `awin_import`:
`stage`, `cause` (a reason code or an exception type name), `dry_run`, page and item counts, the outcome counts and the
skip-reason histogram. Nothing in it can hold a URL, copy or token. A run that completes sends nothing. Sentry is
started only **after** every request has finished (`report_to_sentry`, like `check_offer_health`), because the SDK's httpx
integration would otherwise record each request. A test asserts it is not initialised while the client is called.
Without `SENTRY_DSN` nothing is sent; if sending fails stderr names the error type and the exit code is unchanged.

## The HTTP client (`app/core/awin_client.py`)

httpx, fixed host, TLS verification on, `trust_env=False` (proxy variables ignored), redirects **not followed** (a
redirect is `redirected`; following one would carry the header elsewhere), timeouts (connect 5 s, read 20 s, write 10 s,
pool 5 s) and a 30 s deadline per attempt, a 4 MiB response cap enforced while streaming, error bodies never read.
Failures become a reason code and nothing else (`AwinError`): `auth_failed` (401 or 403: stop at once), `rate_limited`
(429), `server_error` (5xx), `timeout`, `connection_error`, `tls_error` (not retried), `client_error` (other 4xx),
`redirected`, `unexpected_status`, `response_too_large`, `invalid_json`, `unrecognised_response`. Only 429, 5xx,
timeouts and dropped connections are retried: at most 4 attempts, backoff 1, 2, 4 seconds, `Retry-After` honoured (whole
seconds, capped at 60 s). The error is raised outside the `except` block, so it has no `__cause__` or `__context__` that
could hold a library message.

**Throttle.** Awin allows 20 calls a minute per user. `SlidingWindowLimiter` guarantees no more than 20 calls in any
60 seconds (checked in tests with a fake clock over hundreds of calls and concurrent callers), and **every attempt,
retries included, passes through it**. The limit is per Awin user, so another tool using the same token shares it; a
429 is retried, then reported.

## Unverified assumptions the first dry-run confirms or breaks

Awin's page does not document these. Each is handled defensively; the **first dry-run** shows which are true.

1. **Response envelope.** The parser accepts a bare JSON list, or an object whose `data` is a list with an optional
   `pagination.totalPages` (a non-negative integer, used only as a hint to stop early). Anything else fails as
   `unrecognised_response` (nothing is guessed). *If the real envelope is different, the dry-run fails on page 1.*
2. **Last page.** Assumed to be an empty or short page (fewer than `pageSize` items). If Awin keeps returning full pages
   past the end, the repeat check stops the run (`pagination_repeat`); if it returns a short page before the end the run
   stops early silently, which only an item count against Awin's UI would show.
3. **`page` is 1-based** and `pageSize` can be any value 10 to 200 (the documented range).
4. **Item shapes and types.** `promotionId` and `advertiser.id` are integers, `advertiser.joined` a boolean,
   `regions.list[].countryCode` upper-case, dates exactly `YYYY-MM-DDTHH:MM:SS.fff` (an optional `Z` is tolerated). An item
   that differs is skipped with a reason code, so a wrong assumption shows as a skip count (for example everything
   `invalid_date` or `invalid_id`).
5. **`endDate` semantics.** Read as the instant given in UTC. If Awin means "through the end of that day", the offer is
   treated as ending up to a day early (it can only be published again by an editor, and the expiry job would expire a
   published one early). Check one real promotion.
6. **`urlTracking` host.** Assumed to be on `awin1.com` (or a subdomain), the one host the allowlist holds, which is
   itself unverified. If Awin uses another domain, every item is skipped as `tracking_host_not_allowed` and nothing is
   imported; fix by adding the confirmed host to `PROVIDER_HOSTS` in a reviewed change
   ([freejoy-affiliate.md](freejoy-affiliate.md#provider-host-allowlist)), never by relaxing the check.
7. **`url` is the merchant's own page** (not a tracking URL). It becomes `source_url` and the merchant website's origin.
8. **A `voucher` object exists only on vouchers, `code` may be null**, and `voucher.code` is a string when set.
9. **Error status codes.** 401 or 403 mean a bad token, 429 a throttle; error bodies are never read, so their shape does
   not matter.
10. **`updatedSince`** filters on a date Awin maintains and takes `yyyy-MM-dd`.

### The owner's first run (a real token is an owner step)

1. Put `AWIN_PUBLISHER_ID` and `AWIN_OAUTH_API_TOKEN` in `fastapi_backend/.env` (never commit it).
2. `uv run python -m commands.import_awin_offers --dry-run --limit 20 --max-pages 1`. It touches no database.
3. Read the counts. Exit 0 with mostly `valid` means 1, 3 and 4 held. `unrecognised_response` breaks 1;
   `invalid_date` or `invalid_id` breaks 4; `tracking_host_not_allowed` breaks 6; `auth_failed` is the token or
   publisher id.
4. Only then run it without `--dry-run` against a database you own, then open the admin offers list: the drafts are in
   "Imported (uncategorised)".

## Known limits

- **Not in the feed any more.** An offer whose promotion has left the feed (ended, left the programme) is not
  archived or expired by this command; drafts stay drafts, and published offers still expire through
  `check_offer_health` when their `end_at` passes.
- **An item that stops validating** (for example its tracking host changed) is skipped and counted; its existing offer is
  left alone and **not** marked as changed.
- **No review queue yet.** `source_changed_at` is stored for one; nothing reads it. Likewise there is no admin action to
  map an advertiser to an existing merchant, so an editor who wants an existing merchant changes the offer's merchant
  and leaves the imported one inactive.
- **No public exposure.** Drafts are never in the public catalogue. The holding category is hidden only because it is
  disabled: an admin who enables it by hand would make every draft in it eligible to publish (the importer then refuses
  to run, but does not undo that).
- `HASH_VERSION` (in `awin_promotions.py`) must be bumped when the normalisation changes; after a bump every promotion
  reads as changed once (untouched drafts refresh, edited offers get a marker).
- Copy is the merchant's, not original editorial writing: the editor reviews it before publishing.

## Tests

`tests/core/test_awin_config.py`, `test_awin_client.py` (request shape, header-only token asserted against the URL, logs,
output, `repr`, exceptions and `__cause__`; every status, retry, `Retry-After`, redirect, size cap, malformed JSON, every
envelope, deadlines, `trust_env`), `test_call_limiter.py` (never more than 20 in any 60 s, fake clock),
`test_plain_text.py`; `tests/services/test_awin_promotions.py` (every rejection, look-alike hosts, dates, lengths),
`test_awin_offer_mapping.py` (fingerprint per editable field), `test_awin_offer_import.py` (create, idempotency, refresh,
the protection cases, merchants, slugs, audit), `test_awin_import_run.py` (paging, limits, batching, failure accounting,
dry run); `tests/commands/test_import_awin_offers.py` (flags, output, exit codes, settings, Sentry content and
ordering); `tests/commands/test_import_awin_offers_e2e.py` against Postgres (migration and constraints, cascades,
downgrade, upserts, audit rows, the real admin edit and publish paths, simultaneous runs, a failing batch, a held row
lock, and subprocess runs of the CLI with a fake client in a process whose non-loopback connections are blocked,
`tests/commands/awin_cli_runner.py`). The e2e file runs only with `TEST_DATABASE_URL` set to a local `*_test` database.
