# Audit log

Every admin or organisation change writes one row to `audit_logs`; platform admins read them through one list
endpoint and the admin audit-log page. App settings, feature flags, email templates, SEO meta and redirects are not
audited.

## Row shape

`audit_logs` has `user_id` and `organization_id` (neither is a foreign key, so
rows outlive users and organisations), `action` (`<area>.<verb>`), `resource`, `resource_id`, a small
`metadata` JSON (`before`, `after`, `details`, `request_id`, `actor_role`), `ip_address` and `user_agent` (admin
routes and billing routes). Metadata passes through `app.services.audit.sanitize`
(secret-looking keys and values are redacted, size is bounded). Never log bodies, tokens, card data, Stripe
customer ids, return URLs or connection strings.

### Metadata shape

`metadata` is a JSON object (or null on very old rows), always sanitised. Keys, all optional: `actor_role`
(admin routes), `request_id` (admin and billing routes), `before` and `after` (changed fields only) and
`details` (small extra facts such as `plan`, `interval` or a payment method id). Redacted values read
`[REDACTED]`; an over-long object ends with `_truncated` or `[TRUNCATED]`. 

### Indexes and query cost

Indexes for the list filters: `(user_id, created_at)`, `(action, created_at)`, `(organization_id, created_at)`
and `(created_at)`, all created by the baseline `0001`. `created_from` / `created_to` use the `created_at` columns of those indexes.

Known costs: each page runs one `count(*)` over the filtered rows (an index range scan;
cheap while the table is small, linear in the matching rows later), and `OFFSET` pagination reads and discards
the skipped rows, so `offset` is capped at 10,000. Narrow the filters or the date range to reach older rows. If the
table grows into the millions, switch to keyset pagination on `(created_at, id)` and drop the exact total.
The baseline builds its indexes without `CONCURRENTLY`, which blocks writes during the build; any later index on a large table must be built `CONCURRENTLY` in an autocommit block with
`if_not_exists` and a matching downgrade.

## Covered actions

| Action | Written by | Organisation |
| --- | --- | --- |
| `admin.users.update`, `.delete`, `.ban`, `.unban`, `.grant_admin`, `.revoke_admin` | `AdminUsersService` | no |
| `admin.users.grant_admin` (from `commands.make_admin`) | `AuditService.record_system_action`: `user_id` null, `metadata.actor` the command | no |
| `admin.subscriptions.cancel`, `.refund`, `.grant` | `AdminSubscriptionsService` | yes |
| `admin.offers.create`, `.update`, `.publish`, `.unpublish`, `.expire`, `.archive`, `.verify`; `admin.offer_links.create`, `.update`; `admin.categories.create`, `.update`, `.enable`, `.disable`; `admin.merchants.create`, `.update`, `.activate`, `.deactivate` | `AdminOfferService`, `AdminCategoryService`, `AdminMerchantService` ([backend-admin-catalogue.md](backend-admin-catalogue.md)) | no |
| `org.create`, `org.update` | `OrganizationsService` | yes |
| `billing.checkout`, `billing.change_plan`, `billing.cancel`, `billing.resume` | `OrgBillingService` | yes |
| `billing.delete_payment_method`, `billing.set_default_payment_method` | `OrgBillingService` | yes |

Transactions: the organisation and admin-user rows are staged in the same transaction as the change, so
both commit or both roll back. Billing changes happen at Stripe, which cannot join a database transaction: the row
is written after Stripe accepts the change. If that write fails the session is rolled back, an error is
logged (the fields are in the message text as `organization_id=... audit_action=... idempotency_key=... request_id=... error_type=...`, because the JSON formatter keeps only a fixed set of extras) and reported to Sentry as a surrogate carrying only the error type, and the request still succeeds,
because the change stands at Stripe and a 500 would invite a repeat. Operators reconcile from that log line and the
Stripe webhook (the source of truth for subscription state). Billing rows carry the client IP, user agent and
request id, like admin rows. A client that retries a plan change, cancel, resume or checkout without an `Idempotency-Key` gets a fresh Stripe key, as before.
Admin refunds write an intent row before calling Stripe. Refused or failed requests (403, 404, 409, 502) write no row.

Not audited, on purpose: account deletion (`DELETE /v1/me`; the user's own, account-level action, like profile and
two-factor changes), reads, opening the billing portal (no change is made by the call itself), Stripe webhook
processing (system driven, recorded in `stripe_events`), sign-in and session refresh (login history), and a user's
own profile, settings and two-factor changes (account level, not an admin or organisation change). Add them if your product needs them.

## API

`GET /v1/admin/audit-logs`, permission `audit.read` (a `super_admin` with `*` holds it). 401 without a session,
403 for a non-admin, an admin without the permission and every ordinary user, including an organisation owner.

Query: `organization_id`, `user_id` (exact ids), `action` (exact, for example `org.update`),
`created_from` / `created_to` (ISO 8601, inclusive; 422 when `created_from` is after `created_to`), `limit`
(1 to 100, default 50), `offset` (0 to 10,000). Newest first. Response:
`{success, data: {entries: [...], total_count, limit, offset}}` with `Cache-Control: no-store`. Listing writes no row.

Tests: `tests/api/test_audit_logs_e2e.py` (real Postgres via `TEST_DATABASE_URL`), the audit assertions in `test_organizations_e2e.py` and `test_org_billing_e2e.py`, and the unit tests in
`tests/services/test_audit*.py` and `test_org_billing.py`.
