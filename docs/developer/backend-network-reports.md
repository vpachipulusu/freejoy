# Network report importer (`commands.import_awin_reports`)

One command that reads Awin's **own transaction report** for a range of dates and stores each transaction in
`network_report_rows`: the network's transaction id, its commission status, the commission and sale amounts, the
click reference it reported and, when it can be found, the offer, affiliate link and merchant it belongs to. It is
backend only: no route, no UI, no OpenAPI change, no new setting, no new dependency.

```bash
# fastapi_backend/, with AWIN_PUBLISHER_ID and AWIN_OAUTH_API_TOKEN set (and DATABASE_URL unless --dry-run)
uv run python -m commands.import_awin_reports --from 2026-11-01 --to 2026-11-10 --dry-run   # fetch and validate, write nothing
uv run python -m commands.import_awin_reports --from 2026-11-01 --to 2026-11-10             # import
uv run python -m commands.import_awin_reports --from 2026-08-01 --to 2026-11-10 --allow-long-range --max-pages 20
```

**Revenue comes from here and from nowhere else.** `click_events` are counts only (see
[freejoy-affiliate.md](freejoy-affiliate.md#clicks-are-not-conversions)); nothing infers a sale or a commission from a
click. What Freejoy earned is what Awin's report says it earned, row by row.

Code: `commands/import_awin_reports.py` (arguments, output, exit codes, Sentry), `app/services/network_report_run.py`
(windows, the paging loop, the audit row), `app/services/network_report_import.py` (insert, update, matching),
`app/services/awin_transactions.py` (one row as untrusted input), `app/repositories/network_reports.py` (SQL),
`app/core/awin_client.py` (`fetch_transactions` on the same client as the offers importer),
`app/services/awin_tracking.py` (`passive_values`, shared with the offers importer's link check),
`app/models/platform/network_report.py` and migration `0005`.

> **Not verified against the live Awin API.** Everything here was built and tested against fakes
> (`tests/fakes_awin.py`, `httpx.MockTransport`); no request has ever been sent to Awin and the code never uses a real
> token. Awin's public documentation describes the **request** (path, dates, filters, the 31-day cap) but **not the
> response body**, nor paging, so the row shape below is an [assumption](#unverified-assumptions-the-first-dry-run-confirms-or-breaks)
> written so that a wrong guess fails with a reason code instead of storing something odd. The owner's first
> `--dry-run` with the real token is what confirms it.

## What it never does

- **It never infers revenue from clicks**, and never writes to `click_events`, `offers` or `affiliate_links`.
- **It is not scheduled.** Nothing runs it automatically and the housekeeping workflow does not include it; adding it
  is the owner's decision after a first `--dry-run`.
- **It never requests anything but the transactions endpoint**, never a tracking link or a merchant site.
- **It never prints, logs or sends** a URL, the token, an amount, a click reference or a transaction id. Output is
  counts and reason codes; a skipped row is logged as a reason code only.
- **It never guesses.** A status Awin did not document, an amount with more than two decimals, a missing amount, a
  currency that disagrees between the two amounts or is not a two-decimal currency rejects the row with a code, and
  the code is counted. A click reference that matches nothing for certain matches nothing.
- **It never deletes a row, and never erases or moves a stored match** when a status or amount changes later.
  A transaction that stops appearing in a later report keeps its last known state.

## Settings

| Variable | Notes |
| --- | --- |
| `AWIN_PUBLISHER_ID` | Positive integer, as for the offers importer. |
| `AWIN_OAUTH_API_TOKEN` | `SecretStr`; only ever sent in an `Authorization: Bearer` header. Awin also lists an `accessToken` query parameter; it is never used. |

The command exits **2** naming the missing variable when either is empty (a `--dry-run` also needs them: it calls the
API). `DATABASE_URL` is required by the app settings, but a `--dry-run` opens no connection to it.

## Flags and exit codes

| Flag | Default | Meaning |
| --- | --- | --- |
| `--from YYYY-MM-DD` | required | First day (UTC, inclusive). Exactly that shape, not before 2000-01-01. |
| `--to YYYY-MM-DD` | required | Last day (UTC, inclusive); not before `--from` and not after tomorrow (UTC). The range is **at most 31 days** (Awin's documented maximum). |
| `--allow-long-range` | off | Allow more than 31 days. The range is then read in consecutive windows of at most 31 days, each from `00:00:00` to `23:59:59` UTC, one second apart (see [the assumptions](#unverified-assumptions-the-first-dry-run-confirms-or-breaks), 11). |
| `--dry-run` | off | Fetch and validate, write nothing, open no database connection, write no audit row, print counts. |
| `--max-pages N` | 10 (1 to 50) | Most pages fetched in the whole run (across windows). Awin allows 20 calls a minute. The range must need no more windows than this. |

The whole run also has a fixed 20-minute deadline (`run_deadline`, exit 1; stderr says "(at least)" before the counts
because a cancelled commit may be committed and not counted).

| Exit | Meaning |
| --- | --- |
| `0` | The run completed and read the whole range, also when some rows were skipped; a `--dry-run` that stopped at `--max-pages` (it only prints a note, because that is the first-run recipe); an empty report. |
| `1` | The run failed: stderr names the stage, a reason code or error **type** (never text), how much had been committed (those chunks stay committed) and what to do next. One Sentry event is sent (below). This includes **`max_pages_reached`** (a run that writes stopped at `--max-pages`, so part of the range was not read: stderr says `stopped in the window starting YYYY-MM-DD; rerun with --from YYYY-MM-DD`, and the audit row says `incomplete`) and **`no_valid_rows`** (rows arrived and none was valid, in both modes: nothing trustworthy was imported). |
| `2` | Bad arguments (missing or malformed date, a date outside 2000-01-01 to tomorrow, `--to` before `--from`, a range over 31 days without `--allow-long-range`, `--max-pages` out of range, **a range that needs more windows than `--max-pages`**, an unknown flag) or missing `AWIN_*` settings, all before any request. |

stdout is counts only:

```
import-awin-reports: fetched 120 transaction(s) in 1 page(s) for 2026-11-01 to 2026-11-10; 118 valid, 2 skipped.
import-awin-reports: inserted 100, updated 10, unchanged 8, unmatched 40.
import-awin-reports: skipped by reason: invalid_amount 1, unknown_status 1.
import-awin-reports: not matched because: merchant_mismatch 3.
import-awin-reports: 1 rejected row(s) are already stored and were left as they were.
```

"Unmatched" counts the valid rows (inserted, updated or unchanged) that ended with no offer: see
[matching](#matching-a-row-to-an-offer); the next line says why for the rows where it is known. A dry run prints the
first line (prefixed `dry run, nothing was written.`) and the skip reasons, and no write counts. A dry run that stops
at the page limit adds `stopped at --max-pages: more transactions may exist`.

Skip reasons (`SkipReason`): `invalid_item`, `invalid_id`, `invalid_advertiser`, `invalid_date`, `invalid_status`,
`unknown_status`, `invalid_amount`, `invalid_currency`, `currency_mismatch`, `unsupported_currency`,
`invalid_click_ref`, `duplicate_in_feed`. Match problems (`MatchProblem`, the row is still stored, unmatched):
`merchant_mismatch`, `too_many_candidates`. `rejected_known` counts rejected rows whose transaction id is already
stored (the stored row is left as it was), so a stored row that later fails validation is visible.

## How a run works

1. The dates become windows of at most 31 days. For each window the command requests page 1 of
   `GET /publishers/{id}/transactions/` with `startDate`, `endDate`, `dateType=transaction`, `timezone=UTC`, `page`
   and `pageSize=200`.
2. Every row is untrusted input (`parse_transaction`, below); duplicates within a window are dropped
   (`duplicate_in_feed`); the valid rows are written in chunks of at most 200, **each in its own database
   transaction**, which commits once. A failure stops the run; earlier chunks stay. A rerun is idempotent. The stored
   rows for a chunk are read in **one query**, and the merchant and the match are looked up once per advertiser and
   click reference in the chunk, not per row.
3. **A window ends** on a page that is **not exactly 200 rows**: an empty or short page, or one **longer** than asked
   for (that means Awin ignored `page` and `pageSize` and sent everything, so asking again would only repeat it; it is
   still written in chunks). A page of **exactly 200** always leads to one more request, whatever
   `pagination.totalPages` says: the total is not trusted to end a window. A page whose ids were all seen on an earlier
   page of the window fails the run as `pagination_repeat`; so, if Awin ignores paging, a range of **exactly 200**
   rows fails that way (it is not special-cased).
4. After the last page, one audit row is written. A run that stops at `--max-pages` or has no valid row fails (above).

Each chunk's transaction begins with `pg_advisory_xact_lock` on a key of its own (not the offers importer's), with a
30 s `lock_timeout`: two report runs take turns chunk by chunk and can never insert the same transaction twice, a run
that cannot get the lock fails as `lock_timeout`, and an offers import and a report run never wait for each other.
The lock is per chunk, not per run: see [known limits](#known-limits).

## Validation of each row

`parse_transaction` returns a frozen `ReportRow` or a `Rejected(reason)`; a rejection never carries the row.

- **Ids** (`id`, `advertiserId`): positive integers, not strings or booleans.
- **Date** (`transactionDate`): `YYYY-MM-DDTHH:MM:SS[.fraction][Z]` read as UTC (the request asks for `timezone=UTC`),
  years 2000 to 2100; an offset, a date alone or a number is `invalid_date`. The timestamp rule is the offers
  importer's (`parse_utc_timestamp`).
- **Status** (`commissionStatus`): 1 to 32 characters of `[A-Za-z0-9 _-]`, else `invalid_status`; then, case-insensitively,
  one of `pending`, `approved`, `declined`, `paid` or `deleted` (Awin's documented filter values plus `paid`), stored
  one to one, else `unknown_status`. The value as written is kept in `commission_status_raw`. A transaction that
  turns `deleted` updates its row to `deleted`: it stays visible as deleted revenue and is never summed by accident.
- **Amounts** (`commissionAmount.amount`, `saleAmount.amount`): a JSON number or a decimal string in **major units**
  (`1.5` is 1.50), converted with `decimal.Decimal` (a float goes through its shortest `repr`, never `* 100`). Accepted:
  integers, JSON numbers (so `1.5e1`, which JSON decodes to the float 15.0, is 15.00) and decimal strings of at most two
  significant decimals (so `"12.340"` is 12.34; both are pinned by tests). Rejected as `invalid_amount`: negative,
  `NaN`/`Infinity`, exponent notation **inside a string**, thousands separators, signs, spaces, more than two real
  decimals and anything above 999,999,999.99. A missing amount is `invalid_amount` too: a lead without a sale amount is not assumed to be 0.
- **Currency** (`...Amount.currency`): three upper-case ASCII letters (`invalid_currency`), the same on both amounts
  (`currency_mismatch`) and one of GBP, EUR, USD, AUD, CAD, CHF, DKK, NOK, SEK, PLN, NZD (`TWO_DECIMAL_CURRENCIES`):
  minor units are x100 only for a two-decimal currency, so any other code (JPY, KWD, ...) is `unsupported_currency`
  rather than stored at the wrong scale. Adding a code is a reviewed change.
- **Click reference** (`clickRefs.clickRef`, else a top-level `clickRef`): at most 100 of `[A-Za-z0-9_.-]`; empty or
  absent is none; anything else is `invalid_click_ref`. Only the first reference (`clickRef`, slot 1) is read.

`report_hash` is the SHA-256 of the **normalised** row (`HASH_VERSION`, ids, UTC date, mapped and raw status, minor
units, currency, reference), so a reordered object, whitespace, an unused key or `1.5` written as `"1.50"` never
counts as a change at the network. `tests/services/test_awin_transactions.py` pins the digest of a known row; bump
`HASH_VERSION` when the normalisation changes (every stored row then reads as changed once and is rewritten).

## Matching a row to an offer

Only when a row is **inserted**, or **changes and has no match yet or reports a different click reference**. A row that
changes (a status moves, an amount is amended) and already has an offer for the same click reference **keeps its stored
offer and link**: a link deactivated or a second link added since must not rewrite which offer earned a commission. An
unchanged row only has `last_seen_at` moved. When matching runs:

1. **Merchant.** `merchant_imports (provider='awin', external_id=advertiserId)` gives `merchant_id`; none when the
   advertiser was never imported. `merchant_external_id` always keeps the advertiser id as reported.
2. **Link and offer**, by the reported click reference, in this order:
   1. the **active** `affiliate_links` rows of provider `awin` whose `tracking_id` equals it;
   2. else the active `awin` links whose stored `destination_url` carries it as the **whole value** of its `clickref`
      (`awin_tracking.passive_values`, the parser the offers importer already uses; `ref-1` never matches `ref-12`, and
      `ued=ref-1` is not a click reference). Slot 1 only: `clickref2` to `clickref6` are neither read from the report nor
      matched (a known limit).
3. **A candidate must be an Awin click link for the reported advertiser.** The link's destination is parsed
   (`awin_tracking.link_advertiser_id`: `awinmid` on `/cread.php`, `mid` on `/awclick.php`); a link that does not parse
   as an Awin click link is not a candidate, and when links carry the reference but only for **another** advertiser the
   row stays unmatched and is counted `merchant_mismatch`.
4. **No match**, by design, when the row has no click reference, no link carries it, or **more than one** link of the
   reported advertiser does (a wrong attribution is worse than none: `affiliate_link_id` and `offer_id` stay null).
   When **more than 200** links carry the reference the list may be cut short, so nothing is matched and it is counted
   `too_many_candidates` (the queries read 201 and fail closed). Unmatched rows are kept and counted.

Limits, stated plainly:

- `/go/` emits **no click reference today**, so a per-click id is not what links a sale to an offer yet; a row matches
  only if the stored Awin link carries the reference Awin reports (a `clickref` in its URL, or a `tracking_id`). Most
  rows will be unmatched until the links carry one; the unmatched count is how the owner sees that.
- Only the first click reference slot is read and matched; slots 2 to 6 are out of scope.
- A link added **later** is not applied to an unchanged row (and an advertiser imported later is not applied to its
  merchant); the row matches on its next change. Re-matching old rows is a follow-up.

## What is written

`network_report_rows` (migration `0005`, model `app/models/platform/network_report.py`):

| Column | Value |
| --- | --- |
| `provider`, `external_id` | `awin` and the transaction id; unique together. CHECKs: provider `^[a-z][a-z0-9_]{0,39}$`, external id 1 to 64 characters. |
| `offer_id`, `affiliate_link_id`, `merchant_id` | Optional FKs, all `ON DELETE SET NULL`: deleting any of them keeps the reported revenue. |
| `merchant_external_id` | The advertiser id as reported (1 to 64 characters). |
| `transaction_at` | Aware UTC datetime. |
| `commission_status` | `pending`, `approved`, `declined`, `paid` or `deleted` (CHECK). `commission_status_raw` keeps the value as written (1 to 32 characters). |
| `commission_amount_minor`, `sale_amount_minor` | `bigint`, `>= 0` (CHECK): minor units, never a float. |
| `currency` | `^[A-Z]{3}$` (CHECK). |
| `click_ref` | Nullable, 1 to 100 characters. |
| `report_hash` | 64 lower-case hex characters (CHECK). |
| `first_seen_at`, `last_seen_at` | Set on insert; `last_seen_at` moves on every run that sees the row and **never moves backwards** (`GREATEST`); CHECK `last_seen_at >= first_seen_at`. |
| `id`, `created_at`, `updated_at` | The usual mixins. An unchanged row does **not** move `updated_at`. |

Indexes: unique `(provider, external_id)`, `(provider, transaction_at)`, `offer_id`, `affiliate_link_id` and
`merchant_id` (the last two so that the `SET NULL` on deleting a link or a merchant does not scan the table).

The downgrade drops the table: every imported row is lost (Awin keeps its own history, so a later run over the same
dates would bring most back, but anything Awin no longer reports would be gone). Run it only on a throwaway database
or before any report has been imported.

### Upsert

| Situation | Result |
| --- | --- |
| No row for `(provider, external_id)` | Insert; `first_seen_at` = `last_seen_at` = the run's time (`inserted`). |
| Row exists, same `report_hash` | Only `last_seen_at` is written (`unchanged`). A second run over the same report changes nothing else, not even `updated_at`. |
| Row exists, different `report_hash` | Status, amounts, date, reference, `last_seen_at` and (see [matching](#matching-a-row-to-an-offer)) the match are rewritten; `first_seen_at` is kept (`updated`). |

`dateType=transaction` selects by the date of the sale, so the status of a sale moves (pending to approved or
declined) **after** its window was first read. Re-read recent windows regularly (for example the last 31 days plus
your longest validation period) to pick those changes up; a rerun over a window is safe and cheap.

## Audit

One `audit_logs` row per run through `AuditService.record_system_action` (the path the other commands use): action
`admin.reports.imported`, resource `network_report_rows`, no `user_id`, `metadata.actor` = `commands.import_awin_reports`.
`details` is counts and dates only: `status` (`completed`; `failed` or `incomplete` with a `cause` when the run stopped
after a write unit was started, best effort), `from`, `to`, `pages`, `fetched`, `valid`, `inserted`, `updated`,
`unchanged`, `unmatched`, `rejected_known`, the `skipped` and `match_problems` histograms, `stopped_early` and
`resume_from` (the first day of the window an incomplete run stopped in). Both audit writes are bounded by 30 seconds;
when the failed run's row cannot be written, stderr says so with the exception type only. A dry run writes none. A test
greps the stored row for row values.

## Reporting and Sentry

A failed run (exit 1) sends **one** event, `Awin network report import run failed`, level `error`, context
`awin_reports`: `stage`, `cause` (a reason code or an exception type name), `dry_run`, the two dates, page and row
counts, the outcome counts, the skip-reason and match-problem histograms and `resume_from` (a date), through
`app.core.sentry.report_message`. A run that completes
sends nothing. Sentry is started only **after** the HTTP client is closed (`report_to_sentry`, as in the other
commands) because the SDK's httpx integration would otherwise record each request; a test asserts it is not
initialised while the client is called and that the client is closed when it starts. Without `SENTRY_DSN` nothing is
sent; if sending fails stderr names the error type and the exit code is unchanged.

## The HTTP client

`HttpxAwinClient.fetch_transactions(start, end, *, page, page_size)` is the same client as the promotions call: the
same host (`https://api.awin.com`), `Authorization: Bearer` header, TLS verification, no proxy variables, no redirects,
timeouts and 4 MiB cap, the same 20-calls-a-minute limiter that every attempt passes through, the same retries
(429, 5xx, timeouts, dropped connections) and the same reason codes (`auth_failed`, `rate_limited`, `server_error`,
`timeout`, `connection_error`, `tls_error`, `client_error`, `redirected`, `unexpected_status`, `response_too_large`,
`unsupported_encoding`, `invalid_json`, `unrecognised_response`). The publisher id is the client's (set once, as for
promotions), not an argument. It refuses a naive datetime, an end before the start, a range over 31 days or a page size
outside 10 to 200 before any request. The response envelope is accepted as a bare JSON list or `{"data": [...],
"pagination": {"totalPages": n}}` (the total is parsed but not used to end a window); anything else is
`unrecognised_response`.

## Unverified assumptions the first dry-run confirms or breaks

**What the documentation says** (read on **2026-11-10**):

- [Get List of Transactions](https://help.awin.com/apidocs/returns-a-list-of-transactions-for-a-given-publisher.md) (index
  [apidocs/transactions](https://help.awin.com/apidocs/transactions), "Updated on 2026-10-06"): `GET
  /publishers/{publisherId}/transactions/` (note `publishers`, plural, and the trailing slash, unlike promotions);
  required `startDate` and `endDate` (`yyyy-MM-ddThh:mm:ss`, examples with a `Z`); optional `dateType`
  (`transaction`, `validation` or `amendment`, default `transaction`), `advertiserId`, `status` (`pending`, `approved`,
  `declined`, `deleted`), `showBasketProducts` and `timezone` (default `UTC`, marked mandatory in one table); **a
  maximum of 31 days between `startDate` and `endDate`**; `Authorization` header and an `accessToken` query parameter
  are both listed; a `200` with `application/json;charset=UTF-8`; `clickRef` and `clickRef2` to `clickRef6` are
  available to publishers (`orderRef` only if activated, `networkFee` never); transaction `type` values include Sale,
  Lead, Bonus.
- [Introduction](https://help.awin.com/apidocs/introduction-to-awins-apis.md): most APIs use a Bearer token; 20 API calls
  per minute per user; base `https://api.awin.com`.
- [Understanding commission validation](https://help.awin.com/docs/understanding-commission-validation.md): pending,
  approved, declined and paid exist as commission states; it says nothing about the API.
- [Get List of Transactions by id's](https://help.awin.com/apidocs/returns-a-list-of-transactions-for-a-given-publisher-by-ids.md)
  adds only an `ids` parameter; it is not used.
- The [transaction notification](https://help.awin.com/developers/docs/transaction-notifications.md) page describes a
  webhook with different field names (`transactionId`, `commission`, `transactionAmount`); it is **not** the API
  response and was not used.

**What it does not say, and the code assumes** (each fails with a reason code when wrong):

1. **Paging.** The documentation mentions none. The client sends `page` (1-based) and `pageSize` (200). *If Awin ignores
   them it returns the whole range each time: a range of fewer than 200 rows completes (a short page) and one of more than
   200 completes in chunks (an over-long page ends the window); a range of exactly 200 repeats page 1 and fails as
   `pagination_repeat`. If Awin rejects unknown parameters the dry run fails on page 1 as `client_error`.*
2. **The response envelope**: a bare list, or `{"data": [...], "pagination": {"totalPages": n}}`. Else
   `unrecognised_response` on page 1.
3. **The row's field names**: `id`, `advertiserId`, `transactionDate`, `commissionStatus`, `commissionAmount` and
   `saleAmount` as `{"amount", "currency"}`, `clickRefs.clickRef` (or a top-level `clickRef`). Wrong names show as
   every row `invalid_id`, `invalid_advertiser`, `invalid_date`, `invalid_status` or `invalid_amount`.
4. **Types.** Ids are integers; `amount` a number or decimal string; `currency` upper-case ISO.
5. **`transactionDate` format** is the documented request shape (`YYYY-MM-DDTHH:MM:SS`, `Z` tolerated) and, because the
   request says `timezone=UTC`, is UTC. A format with an offset is `invalid_date` for every row.
6. **Amounts are in major units** with at most two decimals (`12.5` is 12.50). A network that reports minor units
   would store 100 times too much and no check can tell: look at one real row against Awin's UI.
7. **Status vocabulary.** The API's documented filter is `pending`, `approved`, `declined`, `deleted`; `paid` is assumed
   to be possible and all five are stored one to one. If Awin reports another word it is counted as `unknown_status`
   until a reviewed change maps it. Whether a paid commission is reported as `approved` plus a separate flag is unknown,
   so `paid` may never occur.
8. **Both amounts are present** and share a currency. A lead with no sale amount would be `invalid_amount`.
9. **A click reference is a short token** of `[A-Za-z0-9_.-]`. Anything else is `invalid_click_ref`.
10. **The click reference Awin reports is the `clickref` (slot 1) of the link that was followed**, and the stored links
    carry one. Today `/go/` sends no per-click reference, so matching depends on what the stored Awin links contain.
11. **`endDate` is inclusive to the second and timestamps have whole seconds.** The windows (`00:00:00` to `23:59:59`,
    the next starting one second later) only cover the range without a gap or an overlap if that holds. If Awin treats
    `endDate` as exclusive, a transaction at exactly `23:59:59` is missed; if timestamps carry fractions, one in the last
    second of a window could fall in the gap. `transaction_at` is not checked against the requested window and
    overlapping windows are not detected, because Awin's end-date and time zone semantics are unverified.
12. **Currencies** in the response are among the two-decimal codes in the allow-list; any other is `unsupported_currency`.

### The owner's first run (a real token is an owner step)

1. Put `AWIN_PUBLISHER_ID` and `AWIN_OAUTH_API_TOKEN` in `fastapi_backend/.env` (never commit it).
2. `uv run python -m commands.import_awin_reports --from <a recent day> --to <the same day> --dry-run --max-pages 1`. It
   touches no database.
3. Read the counts. Exit 0 with mostly `valid` means 2 to 5, 8 and 9 held. `unrecognised_response` breaks 2; `client_error`
   may be 1 (the parameters) or the dates; `invalid_id`, `invalid_advertiser`, `invalid_date`, `invalid_status` or
   `invalid_amount` in numbers break 3 to 6 and 8; `unsupported_currency` is 12; `unknown_status` is 7; `invalid_click_ref` is 9; `pagination_repeat`
   is 1; `auth_failed` is the token or publisher id. Compare one row's amount and status with Awin's own report (6).
4. Only then run it without `--dry-run` against a database you own and read
   `SELECT commission_status, currency, count(*), sum(commission_amount_minor) FROM network_report_rows GROUP BY 1, 2`
   (amounts of different currencies must not be summed together). A high `unmatched` count is expected until the links
   carry a click reference.

## Known limits

- **No re-matching of old rows** and no admin view yet: nothing reads `network_report_rows` except SQL by hand.
- **Only click reference slot 1** is read and matched (`clickRef` against `clickref`); `clickref2` to `clickref6` are out of
  scope.
- **Not scheduled** and no retention rule: rows are kept until a person decides otherwise (they hold amounts and a
  reference, no personal data, no IP or user id).
- **A transaction Awin deletes** is updated to `deleted` if Awin reports it so; one that simply stops appearing keeps its
  last stored state. The command never deletes.
- **Overlapping runs.** The run lock is taken per chunk, not per run, so two runs over the same dates take turns chunk by
  chunk and an older run that is slower can briefly store a stale status after a newer one; the next run corrects it.
  `last_seen_at` never moves backwards (`GREATEST`), so it is not the symptom.
- **Time zones.** Windows are UTC days. A report read in another zone's days will not line up with Awin's UI.
- **Rate limit.** The limit is per Awin user, so another tool using the same token shares it.
- **Windows and `transaction_at`.** A row is not checked against the window that returned it, and overlapping windows are
  not detected (assumption 11).

## Tests

`tests/services/test_awin_transactions.py` (every reject reason, amount edge cases, a pinned hash and its stability),
`tests/core/test_awin_transactions_client.py` (request shape, header-only token, envelopes, failures over
`httpx.MockTransport`), `tests/services/test_network_report_import.py` (insert, update, unchanged, matching by tracking id
and by `clickref` in the URL, by merchant only, unmatched, ambiguous, another advertiser's link, the candidate cap, slot 1
only, a stored match surviving a status change, one query per kind, `deleted`), `test_network_report_run.py` (windows, paging, limits,
the dry run, failure accounting, the audit row, a busy lock, the deadline); `tests/commands/test_import_awin_reports.py` (flags,
output, exit codes, settings, Sentry content and ordering, over `httpx.MockTransport`);
`tests/models/test_network_report_model.py`; `tests/commands/test_import_awin_reports_e2e.py` against Postgres (migration
and constraints, upsert idempotency, matching, `SET NULL` when an offer, link or merchant is deleted, the audit row, a held
run lock, simultaneous runs, subprocess runs of the CLI with a fake client in a process whose non-loopback connections are
blocked, `tests/commands/awin_reports_cli_runner.py`). The e2e file runs only with `TEST_DATABASE_URL` set to a local
`*_test` database.
