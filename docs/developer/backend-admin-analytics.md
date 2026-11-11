# Platform admin analytics and Stripe sync

Routes under `/v1/admin/analytics`, all platform-admin only (`require_admin`, then no-store):

| Route | Permission | Returns |
|---|---|---|
| `GET /overview` | `users.read` | user counts (total, verified, new this month, active in 30 days, admins), organisation counts |
| `GET /revenue` | `subscriptions.read` | MRR and ARR in minor units, per-plan split, organisations by status and tier, cancelling and granted counts |
| `GET /system` | `settings.read` | row counts of settings, flags, templates, SEO, redirects, audit logs, Stripe events, admins |
| `GET /clicks` | `offers.read` | outbound clicks per offer per UTC day for one merchant over a date range (see below) |
| `POST /subscription-sync` | `subscriptions.sync` | drift report between Stripe and the organisations table; one audit row |

No migration and no model change: everything reads existing tables (`click_events`, `offers` and `merchants` for the click report).

## Revenue

Computed from organisation billing data, never one Stripe call per customer. Organisations in `active` or
`past_due` are grouped by `stripe_price_id` in one query. Each *configured* price id
(`STRIPE_PRICE_ID_*`) is read once from Stripe for its unit amount; yearly prices count as one twelfth per month.
Trials pay nothing yet and are not revenue. A billed organisation whose price id is not configured or whose price
cannot be read is counted in `unpriced_organizations` and `complete` is `false` (the totals are then a floor).
The figure is **list-price MRR**: subscription quantity, coupons and tax are not modelled. Price amount and currency
come from the same `/prices/{id}` read; a price whose currency differs from the platform `currency` setting is
counted as unpriced (`complete=false`) rather than added. Stripe prices must be in the platform currency (GBP unless the `currency` app setting says otherwise): a price in another currency is excluded from admin MRR and shown with the platform symbol on `/pricing`. ARR is exact (monthly x 12 plus yearly prices as paid).
Price reads are cached in process for 5 minutes (`price_cache.py`; failures are never cached), and the request's
database transaction is ended before them, so no connection is held during Stripe calls. Admin grants (`granted_plan`) are complimentary and never revenue.

## Merchant click report

`GET /v1/admin/analytics/clicks?merchant_id=&from=&to=` is the report an owner sends to a retailer listed directly, who
has no network report: what Freejoy delivered is its own click count. Platform admin with `offers.read` (the same
permission as the merchants API the picker reads), `Cache-Control: no-store`, rate limited like the other routes.
Code: `app/api/v1/admin_analytics_clicks.py` (shapes), `app/services/merchant_click_report.py` (rules),
`app/repositories/merchant_clicks.py` (SQL). The admin page is `/admin/analytics` ("Clicks by merchant") with a CSV
download; see [freejoy-affiliate.md](freejoy-affiliate.md#what-to-tell-a-retailer-about-clicks).

**Parameters.** `merchant_id` (required, an id), `from` and `to` (dates, `YYYY-MM-DD`, UTC, **both days included**).
`from <= to`, at most **366 days** counting both ends, and years 2000 to 2999. A date is exactly `YYYY-MM-DD`: a Unix
timestamp, a date-time and `20260310` are refused. Anything else is `422`: a missing or malformed value answers `{"success": false, "error": "Invalid request"}`; a reversed, too long or out of window range
answers `422` with `code: "invalid_range"`. An unknown merchant is `404` with `code: "not_found"` (the same answer as
`GET /v1/admin/merchants/{id}`); a deactivated merchant still reports, because its past clicks exist. The 404 and the
`invalid_range` 422 carry `Cache-Control: no-store` like the success. The other refusals (401 and 403 from the
dependencies, and the plain 422 of a malformed parameter) are produced by shared handlers, so they carry no
`no-store` header: they hold no report data.

**Response** (`data`): `merchant` (`id`, `name`, `slug`), `from_date`, `to_date`, `total_clicks` and
`excluded_bot_clicks` (over every offer of the merchant), `offers_total`, `max_offers`, `offers_truncated`, `offers`
(each `offer_id`, `slug`, `title`, `total_clicks`, `days`: `[{date, clicks}]`, only days with clicks, oldest first),
`retention` (`days`, `earliest_complete_date`, `starts_before_retention`) and `basis` (`definition`, `bot_rule`: fixed
sentences saying what a click is and which rule filtered bots). **Counts only**: no referrer host, no user agent, no
link or destination, nothing about a visitor.

**The partner API** (FREEJ-59, `GET /partner/v1/clicks`, [backend-partner-api.md](backend-partner-api.md#click-counts-get-partnerv1clicks-freej-59)) gives a retailer its own counts through a separate, narrower query with the same rules (UTC days, bots left out, the same order); a Postgres test shows both give the same per offer per day counts.

**What a click is.** One visitor sent through a Freejoy link to the destination (one `click_events` row). It is not a
sale, a sign-up or a claim, and not a unique person: a person who clicks twice counts twice. Nothing is deduplicated.

**Bot rule.** No earlier analytics counted clicks, so this report sets the rule: rows whose `user_agent_class` is `BOT`
are left out of every count and reported once, in `excluded_bot_clicks`; every other class and a missing class (`NULL`)
is counted. That is the only filtering (the class is a coarse label computed in the `/go/` route; it does not guarantee
that no automated traffic remains).

**What `excluded_bot_clicks` means.** Since FREEJ-62 the click endpoint records at most 60 `BOT` clicks a minute for one
offer (`OUTBOUND_OFFER_BOT_POLICY`; the rest are redirected and not stored), in a bucket separate from the 600 a minute
for every other class (`OUTBOUND_OFFER_POLICY`), so automated traffic cannot use up the budget genuine clicks need. The
figure is therefore a **lower bound** on automated traffic, not a full count; the admin page says "minimum" and the
`bot_rule` text says so. Bot rows stored before the change shared the 600 budget with every other class. The residual: A flood that does not identify itself as automated still fills the main budget. The reported count is then not lower than the genuine count, but genuine clicks in a flooded minute are still not recorded and cannot be told apart from the flood afterwards. Because IPv6 addresses are grouped by /64, the ten addresses can be one host. FREEJ-62 only removes the bot-classed route to this. It is not handled
here. See [freejoy-affiliate.md](freejoy-affiliate.md).

**Days.** A day is a UTC calendar day: a click at 23:59:59 UTC belongs to that day, 00:00:00 to the next, whatever the
database session time zone (`timezone('UTC', occurred_at)`). `occurred_at` is already truncated to the minute. The query
range is half open on UTC midnights (`from 00:00` up to `to + 1 day 00:00`).

**Offers and zero clicks.** Offers that are or were live (`PUBLISHED`, `EXPIRED`) always appear, with 0 and no days when
they had no clicks in the range, so the retailer sees every listing. A `DRAFT` or `ARCHIVED` offer appears only when it
had counted clicks in the range. Offers are ordered most clicks first, then title.

**Limits.** At most `MAX_REPORT_OFFERS = 100` offers are returned; `offers_total` is how many qualified and
`offers_truncated` says when some were left out. `total_clicks` still counts every offer. Only days with clicks carry a
row, so the largest answer is 100 offers x 366 days. Each offer's `total_clicks` is the sum of its `days`; when no offer
was cut, `total_clicks` is the sum of the offers' totals too (the statements below may see different snapshots, so the
table always adds up), and when offers were cut it is the merchant total from its own statement.

**Whose clicks.** Clicks follow the offer's **current** merchant: an offer moved to another merchant takes its whole
click history with it, and the first merchant's past reports can no longer be reproduced.

**Retention.** Click rows are deleted after `CLICK_RETENTION_DAYS = 395` (`app/core/click_retention.py`, shared with
`commands.prune_click_events`). `earliest_complete_date` is the first UTC day all of whose clicks are still kept (the
day holding the cutoff is partly gone unless the cutoff is exactly midnight; the daily job may also lag a day; a manual
`prune_click_events --days` below 395 deletes more than this date admits, so the date is then too early), and
`starts_before_retention` is true when `from` is earlier. A range that starts earlier is not refused: its early days
are simply too low, and the page says so.

**Cost.** Four statements however many rows: the merchant lookup; the merchant totals; the offers with their counts and the full offer
count (one grouped query with a window count); the per-day counts for the offers returned (one grouped query). No
migration: the `(offer_id, occurred_at)` index serves the per-day query and small merchants; for a merchant with a large
share of all clicks the planner scans the range through `ix_click_events_occurred_at` or sequentially. Measured on a
throwaway database with 600,000 click rows over 395 days: a merchant with 60 offers and about 100,000 clicks took 14 to
88 ms for the offers query and 8 to 58 ms for the per-day query; a merchant with 3 offers and 5,000 clicks took 1 to 3 ms
(nested loop on the `(offer_id, occurred_at)` index).

Tests: `tests/repositories/test_merchant_clicks_e2e.py` (real Postgres, including the four-statement proof through the service), `tests/services/test_merchant_click_report.py`,
`tests/api/test_admin_analytics_clicks.py` (permissions, validation, `no-store`) and
`tests/api/admin_analytics/test_clicks_routes.py` (real Postgres end to end).

### Frontend and the CSV file

`/admin/analytics` shows "Clicks by merchant": a GET form (merchant, from, to, kept in the URL; default the last 30
days; no `min` or `max` on the dates, a refused range runs no report and shows a notice), the report table (offer,
total, and the days behind a disclosure) and "Download CSV". The page lists at most the **31 most recent days with
clicks per offer** (`MAX_DAYS_SHOWN`) and says how many earlier days are not shown; the total per offer is still the full
total, and the CSV has every day. The notes say what a click is, that bots are left out, when the range starts before
the data, and when the offer limit cut the list ("the table and the CSV file list only these 100, so the file adds up to
less"). A merchant named in the link that is not among the first 100 in the picker is fetched by id and added to it.
The page and the `exportMerchantClicksAction` server action both check the admin gate on the server; the browser never
calls the API. The page announces progress and the summary through one status region.

The file is built by the pure `buildClicksCsv` (`src/lib/admin-clicks/csv.ts`): comma separated, UTF-8 with a byte-order
mark, CRLF line endings. A field holding a comma, semicolon, tab, quote, CR or LF is quoted (quotes doubled). **Formula
neutralising:** a text cell gets a leading `'` when its first visible character is `=`, `+`, `-` or `@` or their
full-width forms (U+FF1D, U+FF0B, U+FF0D, U+FF20), also after leading whitespace (no-break space, U+3000, U+FEFF) or
zero-width characters (U+200B to U+200D, U+2060), and always when it starts with a tab, CR or LF. A formula character
right after a `;`, a tab or a line break inside a cell also gets the apostrophe after that separator (`a;=1+1` becomes
`a;'=1+1`), because a semicolon-locale Excel splits the cell there. A count is written only when it is a finite number.
Columns: `Merchant, Offer title, Offer slug, Date (UTC), Clicks`, one row per offer per day with clicks (counts are plain
numbers), and one row with an empty date and 0 for an offer with no clicks. There are no total rows: the sum of the
Clicks column is the total of the listed offers and a per-offer total is a sum by offer. The file name is
`freejoy-clicks-<merchant slug>-<from>-to-<to>.csv` with the slug reduced to lower-case letters, digits and hyphens and
a date that is not `YYYY-MM-DD` replaced by `date`.

**Size limit.** The action refuses a file over `MAX_CSV_BYTES` (4 MiB of UTF-8) with "This report is too large to
download as one file. Choose a shorter date range." The largest possible report (100 offers x 366 days with 40-character
titles) is 3,399,254 bytes, pinned in `csv.test.ts`; longer titles or slugs can push a full-year report over the limit.

Excel reads it by double-click where the list separator is a comma (UK and US settings); on a system whose list
separator is a semicolon use Data > From Text/CSV.

## Stripe sync

Reads all Stripe subscriptions (`status=all`, 100 per page, at most 20 pages) and compares them with the
organisations. It **reports and does not write**: the webhook stays the single writer of subscription
state, so a sync can never overwrite newer webhook data with an older read. Drift kinds: `unmatched` (no
organisation), `subscription_id`, `status`, `plan`, `cancel_at_period_end`, `missing_in_stripe` (organisation
claims a live subscription Stripe does not have; skipped when the read was truncated). Ended subscriptions of a
customer who has a newer one are counted as `historical_ignored`. At most 100 drift items are returned; the
counts are complete. The read transaction is ended before Stripe is called, so no connection is held during
Stripe calls. Limits: 20 pages (2000 Stripe subscriptions), a 60 second reading budget and 10,000 local billed organisations;
hitting any of them sets `truncated` and skips the `missing_in_stripe` check. Stripe rows that cannot be read (no id or
status) are counted in `skipped_rows` and also skip that check. One HTTP client serves all pages of a run. The sync is rate limited to 3 runs a minute per admin.
A webhook that lands between the Stripe read and the database read can show as transient drift; re-run to confirm.
Audit action `admin.subscriptions.sync` (counts and an outcome, no customer data), written for failed Stripe
reads too (`outcome: unavailable`). Errors: 502 Stripe failed, 503 no key (not audited).

Tests: `tests/services/test_admin_analytics.py`, `tests/core/test_stripe_sync.py`, and the real-Postgres
`tests/api/admin_analytics/` (needs `TEST_DATABASE_URL`, see `backend-admin-subscriptions.md`).
