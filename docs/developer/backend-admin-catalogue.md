# Backend admin catalogue API

Server-authorised create, edit and publish actions for the Freejoy catalogue (categories, merchants, offers,
affiliate links). Authorisation is `require_admin(<permission>)` and the audit trail is `AdminAuditor`
(see [backend-auth.md](backend-auth.md), [backend-audit-logs.md](backend-audit-logs.md) and
[platform-models.md](platform-models.md)). The browser never calls these routes: the Next.js server does, behind
the internal service key and the admin's session.

Code: routers `app/api/v1/admin_offers.py`, `admin_categories.py`, `admin_merchants.py` (schemas in
`admin_offers_schemas.py`, shared pieces in `admin_catalogue_common.py`); services `app/services/admin_offers.py`,
`admin_categories.py`, `admin_merchants.py`, `offer_lifecycle.py`, `offer_rules.py`; repositories
`app/repositories/admin_offers.py`, `admin_categories.py`, `admin_merchants.py`; URL rules
`app/core/catalogue_urls.py`; slugs `app/core/slugs.py`. The public read API is a separate story.

## Permissions

`ADMIN_PERMISSIONS` (`app/core/permissions.py`) is a closed registry. These six were added; nothing in a request can
widen a grant, and `assert_can_grant` refuses to hand out a permission the granting admin does not hold, so an
editor cannot give themselves or anyone else `offers.publish`.

| Permission | Allows |
| --- | --- |
| `offers.read` | List and read offers, categories and merchants (the offer editor needs the pick lists) |
| `offers.write` | Create and edit offers, create and edit affiliate links |
| `offers.publish` | Publish, unpublish, expire, archive and verify an offer (they change what the public sees or the "Last checked" claim) |
| `categories.write` | Create, edit, enable and disable categories |
| `merchants.write` | Create, edit, activate and deactivate merchants |
| `subscribers.read` | Registered only. No subscriber endpoint exists yet; the later subscriber story uses it |

A request without the permission is 403 with the generic `Forbidden` body, before the body is validated and before
any service runs. An unauthenticated request is 401.

## Routes

All responses are `{"success": true, "data": ...}` with `Cache-Control: no-store`; errors use the standard envelope
plus a stable `code` (below). Lists take `limit` (1 to 100) and `offset`.

| Route | Permission | Notes |
| --- | --- | --- |
| `GET /v1/admin/offers` | `offers.read` | `search` (title, slug, summary; `%` and `_` are literal), `status`, `category_id`, `needs_review` (see [the review queue](#review-queue)), newest update first. Rows carry no `review_token` (see [the review token](#the-review-token)) but do carry `import_provider` and `source_changed_at` |
| `POST /v1/admin/offers` | `offers.write` | 201. Always creates a `DRAFT`. `slug` optional |
| `GET /v1/admin/offers/{id}` | `offers.read` | Offer with its affiliate links (admin view: includes `destination_url` and `publisher_notes`), its `review_token` and its `provenance` (null for an offer made by hand) |
| `PATCH /v1/admin/offers/{id}` | `offers.write` | Partial. The slug and the status cannot be changed; nullable fields clear with `null` |
| `POST /v1/admin/offers/{id}/publish` | `offers.publish` | Body `{"review_token"}` (required). See the publish rules and [the review token](#the-review-token) |
| `POST /v1/admin/offers/{id}/unpublish` | `offers.publish` | `PUBLISHED` to `DRAFT`. No body |
| `POST /v1/admin/offers/{id}/expire` | `offers.publish` | `PUBLISHED` to `EXPIRED`. No body |
| `POST /v1/admin/offers/{id}/archive` | `offers.publish` | Terminal. No body |
| `POST /v1/admin/offers/{id}/verify` | `offers.publish` | Sets `last_verified_at` to now. Body `{"review_token", "note"?}` (`review_token` required; the note is audit only) |
| `POST /v1/admin/offers/{id}/dismiss-source-change` | `offers.publish` | Clears the importer's "source changed" marker. Body `{"review_token"}` (required, like `verify`). Answers like the other writes (`review_token`, `previous_review_token`). See [the review queue](#review-queue) |
| `POST /v1/admin/offers/{id}/links` | `offers.write` | 201. `provider`, `destination_url`, `tracking_id?`, `active?` |
| `PATCH /v1/admin/offers/{id}/links/{link_id}` | `offers.write` | Links are deactivated, never deleted (click counts keep their link) |
| `GET /v1/admin/categories`, `GET .../{id}` | `offers.read` | `search`, `enabled` |
| `POST /v1/admin/categories`, `PATCH .../{id}` | `categories.write` | 201 on create. Slug from `name` unless supplied |
| `POST /v1/admin/categories/{id}/enable`, `/disable` | `categories.write` | |
| `GET /v1/admin/merchants`, `GET .../{id}` | `offers.read` | `search`, `active` |
| `POST /v1/admin/merchants`, `PATCH .../{id}` | `merchants.write` | 201 on create. `website` and `logo_url` must be https |
| `POST /v1/admin/merchants/{id}/activate`, `/deactivate` | `merchants.write` | |

Affiliate link routes are not in the original story text; they exist because the publish rule needs an "active
affiliate link" and nothing else could create one.

## Status table

Rows are the current status, columns the action. `-` is refused with **409 `illegal_transition`** and nothing is
written. `verify` never changes the status.

| Current | publish | unpublish | expire | archive | verify |
| --- | --- | --- | --- | --- | --- |
| `DRAFT` | `PUBLISHED` | - | - | `ARCHIVED` | stays `DRAFT` |
| `PUBLISHED` | - | `DRAFT` | `EXPIRED` | `ARCHIVED` | stays `PUBLISHED` |
| `EXPIRED` | `PUBLISHED` | - | - | `ARCHIVED` | stays `EXPIRED` |
| `ARCHIVED` | - | - | - | - | - |

The `expire` transition is also made, with no signed-in admin, by `commands.check_offer_health` for every `PUBLISHED`
offer whose `end_at` has passed ([backend-offer-health.md](backend-offer-health.md)); it writes the same
`admin.offers.expire` audit row with no `user_id` and `metadata.actor` set to the command.

`ARCHIVED` is terminal: an archived offer cannot be edited, verified or given links (409). There is no unarchive
route; restoring one is a database operation for now. The table is code (`TRANSITIONS` in
`app/services/offer_lifecycle.py`) and is pinned by a test.

## Publish rules

Publishing (and re-publishing an `EXPIRED` offer) needs all of:

1. the category is enabled,
2. the merchant is active,
3. at least one active affiliate link, or a `source_url`,
4. `end_at` is null or in the future,
5. `last_verified_at` is set.

If any fails the answer is **422 `publish_blocked`** and the message lists every unmet rule in fixed words.

**Decision: publish rejects, it does not auto-verify.** "Last checked" is a public claim, so stamping it as a side
effect of publishing would let an unchecked offer look checked. The editor calls `verify` (which records who and when
in the audit trail, with the optional note) and then `publish`. The database CHECK `ck_offers_published_is_verified`
is the backstop. There is no staleness window: an old `last_verified_at` still publishes. Add one if the
compliance review asks for it.

A published offer must keep meeting rules 1 to 5 at write time: a `PATCH` that would remove the last destination,
point it at a disabled category or inactive merchant, or set a past `end_at` is refused (422 `publish_blocked`), and so
is deactivating the last active link of a published offer without a `source_url`. The end date is judged only when the
request changes `end_at`: a published offer whose date has already passed (nothing unpublishes it automatically) stays
editable, but cannot be given another past date; all other rules still apply to the merged record.

Disabling a category or deactivating a merchant does **not** unpublish its offers: an admin list can therefore show
`PUBLISHED` offers under a disabled category, and the public read API hides them. Publish and the disable cannot
interleave: publish and offer edits read the category and then the merchant under a share lock (`FOR SHARE`, after the
offer row lock), and the disable or deactivate takes the row lock (`FOR UPDATE`), so one waits for the other's commit
and a publish that starts after a disable commits is refused. The lock order is always offer, category, merchant.
`tests/api/test_admin_catalogue_locks_e2e.py` holds one transaction open and shows the other waits, in both orders.

**URLs of unpublished offers.** Unpublish and archive turn a formerly public offer URL into a 404, not a 410 Gone.
That is a product and SEO decision made here; revisit it if search engines should be told the page is gone for good.

**Concurrent edits.** `PATCH` is last-write-wins. Optimistic concurrency (an `updated_at` precondition) can be added
with the admin UI story. Status changes are serialised by the offer row lock.

## The review token

**The problem.** An editor opens an offer, reads it, and presses Mark verified or Publish. In between, the Awin
importer (see [backend-awin-import.md](backend-awin-import.md)) or another admin may have changed its text, the
destination of a link, or the merchant or category it shows under, so the editor would approve something they never
read. `verify` is a public claim ("Last checked") and `publish` makes the offer live, so both must apply to the version
that was read.

**The design.** The offer read (`GET /v1/admin/offers/{id}`) and the offer returned by every write carry
`review_token`: a SHA-256 hex digest of what a visitor sees and where a click goes. The list rows do **not** carry one
(see below). It is **derived, not stored**, so there is no column and no migration. It covers exactly:

- **the offer**: every editable field (`EDITABLE_FIELDS`), the status and `last_verified_at` (the digest of
  `fingerprint(offer)`, see below), plus the offer id;
- **its affiliate links in click order**, `(created_at, id)`, each as id, provider, destination, tracking id and active
  flag. The first usable link decides where a click goes, so swapping two destinations, re-ordering links or adding,
  deactivating or re-pointing one changes the token;
- **the merchant**: name, website, logo URL and active flag (the website re-points `direct` links at click time, the
  name and logo are on the public page);
- **the category**: name, short name (the public offer page shows both) and enabled flag.

It does not cover slugs, `created_at`, `updated_at` or click counts. The editor's own and other admins' verifies (a new
`last_verified_at`) and the expiry job (a new status) change it, which is intended: the editor reloads and sees the new
state.

It is built in `app/services/offer_fingerprint.py` next to the Awin importer's `fingerprint`, which is a different,
smaller value (offer fields and links without ids) that the importer stores as `applied_content_hash`. The two are
separate functions so the importer's stored hashes never change when the token's coverage does:
`tests/services/test_offer_fingerprint.py` pins the digest of a known offer. The token's own `REVIEW_TOKEN_VERSION` is
bumped when its coverage changes (open tokens go stale and editors reload; it is 2 since the category short name joined).
Both hashes refuse a naive datetime with a `TypeError` instead of reading it as local time. That refusal sits in the
shared `_canonical`, so for that one input `fingerprint()` differs from what it did before; it is unreachable with stored
data (every date column is `timestamptz`) and aware inputs hash as before.

`verify` and `publish` require the token in the request body. In the service the order is: take the offer row lock
(`SELECT ... FOR UPDATE`), read the links, read the category and then the merchant `FOR SHARE`, compute the token,
compare, then write and audit. A mismatch raises before anything is written: **409 `offer_changed`**, no state change
and no audit row. A missing, empty or malformed body field is the router's 422 (`Invalid request`); a well-formed token
that does not match is the 409. The token is not a secret (it is a one-way hash of content the same response returns,
so it leaks nothing the admin cannot already read), so it is compared with plain equality.

**Why nothing can slip in between the check and the write.** The offer's own writers (admin edits, link create and
update, the Awin importer's refresh, the expiry job) take the offer row lock first, so they wait for the transition's
commit or the transition waits for them and then sees the change. Edits of the category or merchant take only their own
row lock, which the transition's `FOR SHARE` read waits for (and which waits for the transition's share lock). The
lock order is always offer, then category, then merchant, which is the order publish already used. There is no cycle: a
merchant or category update holds one row and waits for nothing else, the importer takes the category `FOR SHARE`
(compatible with the transition's share lock) and then the offer lock, and the transition takes the offer lock first
and only share locks after it. The one writer that does not take the offer lock is `commands/seed_catalogue.py`, a
local and test-only command that is not run against a live catalogue.

**Which transitions need it.** Only the ones that make or keep content public as "reviewed": `publish` (this includes
re-publishing an `EXPIRED` offer) and `verify`. There is no separate schedule or approve step. `unpublish`, `expire` and
`archive` only take content off the public site or end an offer's life, claim nothing was reviewed, and must stay
possible for an editor who is looking at an out-of-date page (taking down a bad offer must never be blocked by a stale
token), so they take no body. Edits (`PATCH`, links) are still last-write-wins.

**The list.** The list does not carry a token, and the admin UI's row menu has no Publish or Mark verified: the list
shows none of an offer's description, steps, links or source URL, so approving from it would approve content the editor
never saw. The row menu's "Review" opens the offer's page, which does.

**Write responses carry two tokens.** `PATCH /v1/admin/offers/{id}`, `verify`, `publish`, `unpublish`, `expire` and
`archive` return `review_token` (the state after the write) and `previous_review_token`: the token of the state the write
replaced, computed under the same offer row lock (links, then category and merchant `FOR SHARE`) before anything is
written. A client that loaded token `T` and gets a `previous_review_token` other than `T` knows somebody else changed the
offer before its write, and that the response (and the page after it) already contains their change: the editor's own
response token alone cannot show that, because it includes whatever the others did. For `verify` and `publish` the two
are always equal to what the editor sent (a mismatch is the 409). The response token is also computed inside the
transaction, before `commit()`, so a failure while computing it rolls the write back instead of following a commit
(a test makes it fail and checks nothing was stored). A `verify` or `publish` response's `review_token` is the one a
client that continues (verify, then publish) uses next. Reads, creates and link writes carry no `previous_review_token`. **Link writes (`POST` and `PATCH .../links`) return the link only**: re-read the offer for its new token. A `PATCH` of
an offer is a **full-form** save from the admin UI: every field it sends is written, so an offer-field edit somebody else
made after the editor loaded the page is overwritten by the editor's Save (links, merchant and category are not touched by
it). The `previous_review_token` is how the UI tells the editor that a foreign change was there.

## Review queue

The Awin importer ([backend-awin-import.md](backend-awin-import.md)) writes DRAFT offers with an `offer_imports` row
and sets `offer_imports.source_changed_at` when the source moved on but the offer was not refreshed (it was edited,
published, expired or archived). The admin API reads both.

**The rule, defined once** (`needs_review()` in `app/repositories/admin_offers.py`): an offer *needs review* when it has
an `offer_imports` row **and** (`status = DRAFT` **or** `source_changed_at IS NOT NULL`) **and** its status is not
`ARCHIVED`. An offer made by hand (no `offer_imports` row) is never in the queue, even as a draft.

**Filter.** `GET /v1/admin/offers?needs_review=true` returns exactly that set; `needs_review=false` returns everything
else (the complement, so the two halves always partition the list); omitting it applies no filter. Anything that is not
a boolean is 422. It combines with `status`, `search` and `category_id` (all are ANDed) and the paging. It is a
`LEFT JOIN` on `offer_imports` (one row per offer, `offer_id` is unique), so the page and the count see the same rule.

**Rows and the offer read.** The list selects the join's columns in the same query (no query per row): each row
carries `import_provider` (null for an offer made by hand) and `source_changed_at` (null unless the source moved on).
`GET /v1/admin/offers/{id}` and every write response add `provenance`:
`{provider, external_id, applied_at, source_changed_at}`, or null for an offer made by hand. None of it is part of the
`review_token`: the importer sets and clears the marker without changing the offer's content, so the token does not
change with it.

**Dismissing** (`POST /v1/admin/offers/{id}/dismiss-source-change`, `offers.publish`, body `{review_token}` validated
like `VerifyRequest`): the editor says "I have seen that the source changed". In the service
(`AdminOfferService.dismiss_source_change`) the order is the one of `verify`: offer row lock, `_require_unchanged`
(409 `offer_changed`, nothing written), then the provenance row is read `FOR UPDATE` and, if there is none or its marker
is already null (or the offer is archived), **409 `illegal_transition`**, nothing written. Otherwise
`source_changed_at` becomes NULL, one audit row `admin.offers.source_change_dismissed` (resource `offers`, `before` and
`after` hold `source_changed_at`, `details` the provider and external id) is staged on the same session and the
transaction commits once. The response carries `review_token` and `previous_review_token` computed before the commit;
the two are equal, since the offer itself did not change. Only that one column changes. What it leaves alone, and why,
is in [backend-awin-import.md](backend-awin-import.md#the-review-queue-and-the-importers-hashes).

**Not covered.** The token does not include the marker, so a dismissal clears whatever marker is set when it runs: if
the importer noted a newer source change between the editor's page load and the click, that newer one is cleared too
(the next run only flags a source that differs from the one recorded). A body field naming the marker's timestamp would
close that gap; it is not in the contract.

## Slugs

`app/core/slugs.py`: `slugify` folds accents, turns every other run of characters into one hyphen and cuts at 80
characters on a word edge; `validate_catalogue_slug` accepts lower-case ASCII, digits and single hyphens, 2 to 80
characters. A slug is derived from the title or name unless the request supplies `slug`. It cannot be changed
afterwards (public URLs depend on it). A taken slug is **409 `slug_taken`**: the service checks first and a unique
violation under a race maps to the same answer. A generated slug is never suffixed silently; send an explicit `slug`
to resolve a clash.

## URLs

`app/core/catalogue_urls.py`, applied in the services on every write to `source_url`, `destination_url`, merchant
`website` and `logo_url`: https only; ASCII only (punycode hosts); at most 2048 characters; no whitespace, control or
format characters, no backslash and no percent-encoded control character; no user info (`https://good@evil`); no
IPv6 literal; port 443 or none; the host is a DNS name with at least two labels whose last label is not a number
(decimal digits, or `0x` plus hex digits: the WHATWG "ends in a number" rule), so `127.0.0.1`, `2130706433`,
`0x7f.0.0.0x1`, `1.0x` and `localhost` fail. The scheme and host are lower-cased. Anything else is
**422 `invalid_url`**. Open-redirect style inputs are covered in `tests/core/test_catalogue_urls.py` and the e2e
suite.

## Errors

| Status | `code` | When |
| --- | --- | --- |
| 401 | | No valid session |
| 403 | | Not an admin, or the permission is missing (body is just `Forbidden`) |
| 404 | `not_found` | Unknown offer, link, category or merchant |
| 409 | `slug_taken` | The slug exists |
| 409 | `illegal_transition` | The status table refuses the action, or the offer is archived and edited, or `dismiss-source-change` finds nothing to dismiss (the offer was never imported, its marker is already clear, or it is archived) |
| 409 | `offer_changed` | `verify`, `publish` or `dismiss-source-change` carried a `review_token` that no longer matches the offer, its links, its merchant or its category: nothing was written |
| 422 | `publish_blocked` | A publish rule is unmet |
| 422 | `invalid_url`, `invalid_slug`, `invalid_postage`, `invalid_window`, `unknown_category`, `unknown_merchant` | A value the service rejects |
| 422 | none | Schema validation (`Invalid request`): unknown fields, wrong types, control characters, lengths, and a missing or malformed `review_token` |

`postage_amount_minor` and `postage_currency` (3 upper-case letters) are present exactly when `postage_kind` is
`COST`; `end_at` must be after `start_at`; timestamps must carry a timezone.

`review_token_required` (a `InvalidInput` code) exists only as a service-level guard against an empty token. The
router's schema rejects a missing or empty token first, so the API never returns it.

## Audit

Every write stages one `audit_logs` row on the request's session and commits once after `record`, so the change and
its audit row commit or roll back together (a test makes the audit write fail and checks the offer is not stored).
Actions: `admin.offers.create|update|publish|unpublish|expire|archive|verify|source_change_dismissed`, `admin.offer_links.create|update`,
`admin.categories.create|update|enable|disable`, `admin.merchants.create|update|activate|deactivate` (resources
`offers`, `affiliate_links`, `categories`, `merchants`). Rows hold ids, enums, flags and dates (`before` and `after`). Offer and category titles or names (public content)
and the `verify` note (at most 500 characters, passed through the audit secret-pattern sanitiser) **are** recorded.
URLs, tracking ids and all other free text (summaries, descriptions, steps, notes, eligibility) are **not**: only the
names of the changed fields are. Link rows record the destination **host** only (before and after when it changes),
since the path and query can carry tracking ids. No
subscriber data is written. Enabling a category that is already enabled (and the merchant equivalent) is a no-op that
writes nothing and stages no audit row. Denied requests write no row.

## Tests

- `tests/core/test_catalogue_urls.py`: URL and slug rules.
- `tests/services/test_offer_lifecycle.py`, `test_admin_offers.py`, `test_admin_categories_merchants.py`: rules
  with in-memory fakes (`tests/fakes_admin_catalogue.py`).
- `tests/api/test_admin_catalogue.py`: every route for 401, 403 (no record, missing permission, read-only) and the
  error mapping, with fakes behind the real dependency chain.
- `tests/api/test_admin_catalogue_e2e.py`, `test_admin_categories_merchants_e2e.py`: real Postgres through the
  Alembic migrations (helpers in `tests/catalogue_e2e_support.py`); they run only with `TEST_DATABASE_URL`.
- `tests/api/test_admin_catalogue_locks_e2e.py`: publish against a concurrent category disable or merchant
  deactivate, and against an uncommitted offer or link edit (publish waits, then fails with `offer_changed`), two sessions.
- `tests/services/test_admin_offers_review_token.py` (the token: what it covers and ignores, stale, missing, taken under the
  lock), `tests/api/test_admin_offer_review_token_e2e.py` (the 409 against Postgres after an admin edit, a link change or a merchant or
  category edit, nothing written) and the importer path in `tests/commands/test_import_awin_offers_e2e.py`.
- `tests/repositories/test_admin_offers.py` (the review filter's SQL: one LEFT JOIN, the rule once, every combination),
  `tests/repositories/test_admin_offers_review_queue_e2e.py` (imported draft, published and expired with and without the
  marker, hand-made, archived; every filter combination against Postgres; one query per page),
  `tests/services/test_admin_offers_dismiss.py` (happy path, stale token, nothing to dismiss, archived, audit row,
  tokens), `tests/api/test_admin_catalogue.py` (the query parameter, the row and read fields, the endpoint: 403, 404, 409,
  422) and `tests/api/test_admin_offer_dismiss_e2e.py` (the endpoint against Postgres).
- `tests/services/test_admin_catalogue_atomicity.py`: slug races (unique violation, with and without `__cause__`) and
  a failing audit write, deterministic and without a database.
