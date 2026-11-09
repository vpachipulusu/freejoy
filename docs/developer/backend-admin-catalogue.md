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
| `GET /v1/admin/offers` | `offers.read` | `search` (title, slug, summary; `%` and `_` are literal), `status`, `category_id`, newest update first. Each row carries its `review_token` |
| `POST /v1/admin/offers` | `offers.write` | 201. Always creates a `DRAFT`. `slug` optional |
| `GET /v1/admin/offers/{id}` | `offers.read` | Offer with its affiliate links (admin view: includes `destination_url` and `publisher_notes`) and its `review_token` |
| `PATCH /v1/admin/offers/{id}` | `offers.write` | Partial. The slug and the status cannot be changed; nullable fields clear with `null` |
| `POST /v1/admin/offers/{id}/publish` | `offers.publish` | Body `{"review_token"}` (required). See the publish rules and [the review token](#the-review-token) |
| `POST /v1/admin/offers/{id}/unpublish` | `offers.publish` | `PUBLISHED` to `DRAFT`. No body |
| `POST /v1/admin/offers/{id}/expire` | `offers.publish` | `PUBLISHED` to `EXPIRED`. No body |
| `POST /v1/admin/offers/{id}/archive` | `offers.publish` | Terminal. No body |
| `POST /v1/admin/offers/{id}/verify` | `offers.publish` | Sets `last_verified_at` to now. Body `{"review_token", "note"?}` (`review_token` required; the note is audit only) |
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
importer (see [backend-awin-import.md](backend-awin-import.md)) or another admin may have changed its text or the
destination of a link, so the editor would approve something they never read. `verify` is a public claim ("Last
checked") and `publish` makes the offer live, so both must apply to the version that was read.

**The design.** Every admin offer read (`GET /v1/admin/offers/{id}`, each row of `GET /v1/admin/offers`, and the offer
returned by every write) carries `review_token`: a SHA-256 hex digest of what a visitor sees and where a click goes.
It is **derived, not stored**, so there is no column and no migration. It reuses the Awin importer's fingerprint
(`app/services/offer_fingerprint.py`, `fingerprint`; the importer stores the same value as `applied_content_hash`), so
there is one definition of "this offer changed". It covers every editable field (`EDITABLE_FIELDS`), the status and
`last_verified_at`, and each affiliate link's provider, destination, tracking id and active flag. It does not cover
ids, slugs, `created_at`, `updated_at` or click counts. Link order and the timezone a date is written in do not matter.
A link added, deactivated or re-pointed changes it; so does another admin's verify (a new `last_verified_at`) or the
expiry job (a new status), which is intended: the editor reloads and sees the new state.

`verify` and `publish` require the token in the request body. In the service the order is: take the offer row lock
(`SELECT ... FOR UPDATE`), read the links, compute the token, compare, then write and audit. Every writer of an offer or
its links takes that row lock first (admin edits, link create and update, the Awin importer's refresh, the expiry job),
so a concurrent write waits for the transition's commit, or the transition waits for it and then sees the change.
Nothing can slip in between the check and the write. A mismatch raises before anything is written: **409
`offer_changed`**, no state change and no audit row. A missing, empty or malformed body field is a 422 (`Invalid
request`) and a well-formed token that does not match is the 409. The token is not a secret (it is a one-way hash of
content the same response returns, so it leaks nothing the admin cannot already read), so it is compared with plain
equality.

**Which transitions need it.** Only the ones that make or keep content public as "reviewed": `publish` (this includes
re-publishing an `EXPIRED` offer) and `verify`. There is no separate schedule or approve step. `unpublish`, `expire` and
`archive` only take content off the public site or end an offer's life, claim nothing was reviewed, and must stay
possible for an editor who is looking at an out-of-date page (taking down a bad offer must never be blocked by a stale
token), so they take no body. Edits (`PATCH`, links) are still last-write-wins.

**The list.** `verify` and `publish` can also be pressed from the list's row menu, so each list row carries a token
(computed from one extra query for the links of the page's offers). A token from the list approves the offer as the
list read it, which is less than a person read from the editor page; the guarantee is still "nothing changed since the
screen you clicked on was loaded".

After a `verify` or `publish` the response carries the new token, so a client that continues (verify, then publish)
uses the one it just received. The admin UI reloads after every action and passes the new token to its buttons.

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
| 409 | `illegal_transition` | The status table refuses the action, or the offer is archived and edited |
| 409 | `offer_changed` | `verify` or `publish` carried a `review_token` that no longer matches the offer and its links: nothing was written |
| 422 | `publish_blocked` | A publish rule is unmet |
| 422 | `invalid_url`, `invalid_slug`, `invalid_postage`, `invalid_window`, `unknown_category`, `unknown_merchant`, `review_token_required` | A value the service rejects |
| 422 | none | Schema validation (`Invalid request`): unknown fields, wrong types, control characters, lengths |

`postage_amount_minor` and `postage_currency` (3 upper-case letters) are present exactly when `postage_kind` is
`COST`; `end_at` must be after `start_at`; timestamps must carry a timezone.

## Audit

Every write stages one `audit_logs` row on the request's session and commits once after `record`, so the change and
its audit row commit or roll back together (a test makes the audit write fail and checks the offer is not stored).
Actions: `admin.offers.create|update|publish|unpublish|expire|archive|verify`, `admin.offer_links.create|update`,
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
  lock, list tokens), `tests/api/test_admin_offer_review_token_e2e.py` (the 409 against Postgres after an admin edit or a
  link change, nothing written) and the importer path in `tests/commands/test_import_awin_offers_e2e.py`.
- `tests/services/test_admin_catalogue_atomicity.py`: slug races (unique violation, with and without `__cause__`) and
  a failing audit write, deterministic and without a database.
