# Admin settings

**Last updated:** 2026-10-30
**Scope:** the admin settings area (`/admin/settings`), and how the one platform setting the site reads, the
currency, is stored and used. The "Ideas for later" are ideas, not planned work.

## What the page does today

`/admin/settings` is **read-only**. It shows system statistics and nothing can be edited from it: the page says
"System statistics. Editing settings, flags and templates is not available yet."

- **Path:** `src/app/(admin)/admin/settings/page.tsx` (server component, `force-dynamic`).
- **Data:** `fetchAdminSystemStats()` in `src/lib/api/admin-analytics.ts`, which calls
  `GET /v1/admin/analytics/system` (permission `settings.read`; see
  [backend-admin-analytics.md](backend-admin-analytics.md)).
- **What it shows:** a "System stats" panel with the row counts of application settings, feature flags, email
  templates, SEO entries, URL redirects, audit log entries, Stripe events processed (Stripe Billing module) and
  platform admins. A **Refresh** button reloads it.
- **When the stats cannot load:** a curated error with a **Retry** link back to `/admin/settings`; the status is
  logged, the response body is not shown.
- **Tests:** `src/__tests__/unit/app/admin-analytics-pages.test.tsx` (`admin settings page`) and the
  `settings page shows the system stats` case in `src/__tests__/e2e/admin-analytics.spec.ts`.

There are no General, Theme or Feature flag tabs, no settings form and no feature flag toggles, and no server action
or backend route that writes `app_settings` or `feature_flags`. Earlier versions of this document described those;
they came from the original template and are not in this codebase.

## Currency

The platform currency is the one setting the site reads from `app_settings` (key `currency`).

- **Read path:** the backend serves it at `GET /v1/settings/currency` (see
  [backend-public-settings.md](backend-public-settings.md)); the Next.js wrapper is `src/lib/api/settings.ts`.
- **Default:** GBP (a UK product). The backend (`DEFAULT_CURRENCY` in `app/services/public_settings.py`) and the
  frontend (`DEFAULT_CURRENCY_CODE` in `src/config/currencies.ts`) both default to it when the setting is missing,
  unsupported or the lookup fails. Admin money is formatted by `src/lib/utils/format-money.ts` (en-GB; the currency
  the data carries; the GBP default is applied where the currency is looked up).
- **Supported codes:** the nine in `CURRENCIES` (`src/config/currencies.ts`): USD, EUR, GBP, JPY, AUD, CAD, CHF, CNY
  and INR. The backend list `SUPPORTED_CURRENCIES` must stay equal to it (a test fails when they drift). Any other
  stored value reads as GBP.
- **Stripe prices:** Stripe prices must be in the platform currency (GBP unless the `currency` app setting says
  otherwise): a price in another currency is excluded from admin MRR and shown with the platform symbol on `/pricing`.
- **How to change it today:** there is no admin screen or API for it. The `currency` row in the `app_settings` table
  (`value` is JSONB holding a string such as `"EUR"`, `category` `general`) can only be inserted or updated directly
  in the database by a developer. The change shows on the site within about a minute (the 60 s in-memory cache in
  `src/lib/api/settings.ts`), plus the stale-while-revalidate window described in the backend note.
- **Not settings:** the site name comes from `NEXT_PUBLIC_APP_NAME` / `APP_NAME` and the support email from
  `NEXT_PUBLIC_SUPPORT_EMAIL` (see `src/config/site.ts` and the `.env.example` files), not from this area. There is
  no language setting: the site is English only.

## Data storage

The `app_settings`, `feature_flags` and `email_templates` tables exist in the backend database from baseline `0001`
([platform-models.md](platform-models.md)). Today only `app_settings.currency` is read by the site; the tables are
otherwise only counted for the settings page.

## Access

1. Sign in as an admin user. An existing admin grants admin rights to another user through
   `PUT /v1/admin/users/{user_id}/admin`; the first admin is granted with `uv run python -m commands.make_admin <email>` (see
   [getting-started.md](getting-started.md#first-admin)).
2. Open `/admin/settings`. The backend checks the `settings.read` permission, and every admin page and action verifies the
   admin on the server (the proxy cannot, see the Next.js proxy note in `.claude/CLAUDE.md`).

## Ideas for later

These are not built and not scheduled.

- **Edit the currency from the admin area.** A `settings.write` route with audit logging, a server action in
  `src/app/actions/admin/` and a form on this page, validated against `SUPPORTED_CURRENCIES`. Until then the
  database row is the only way to set it.
- **Feature flags and email templates.** The tables exist and are counted; there is no toggle or editor.
- **Translations.** Only worth doing once the site has a second language.

## Troubleshooting

- **The currency did not change.** Check the row's `value` is a supported code as a JSON string (a lower-case or
  padded code is trimmed and upper-cased; anything else reads as GBP), then allow the cache time above.
- **The page shows an error with Retry.** The system stats call failed: check the backend is reachable and the
  signed-in user has an admin record with `settings.read`.
