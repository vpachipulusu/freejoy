# Running Playwright E2E locally

**Last updated:** 2026-10-30

CI skips Playwright (`E2E_ENABLED=false`), so developers run E2E before opening a PR for user-facing changes. This page is the fast path. Test accounts, the test Firebase project and the test database are described in [testing.md](testing.md).

## The command

From `nextjs-frontend/`:

```bash
npm run test:e2e:local -- src/__tests__/e2e/touch-targets.spec.ts src/__tests__/e2e/theme-contrast.spec.ts
```

Everything after `--` goes to Playwright (spec paths, `--grep`, `--headed`, `--workers=2`). With no spec it runs every spec in the chromium project.

What it does, in order (a timing table is printed at the end):

| Step         | What happens                                                                                                                                                                                                                                                              |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `setup`      | Copies missing `.env`, `.env.local`, `.env.e2e.local` from the main checkout. Links `node_modules` to the main checkout's when `package-lock.json` is identical, otherwise `npm ci --prefer-offline`. Installs Chromium only if it is not in the global Playwright cache. |
| `build`      | `next build --webpack` into `.next-e2e` (never your `.next`). Skipped when the commit, uncommitted edits, port and `.env.e2e.local` are unchanged since the last build.                                                                                                   |
| `serve+warm` | `next start` on a free port, then requests `/`, `/login`, `/signup`, `/pricing` and `/dashboard` once.                                                                                                                                                                    |
| `test`       | `playwright.local.config.ts`: chromium plus the auth setup project, up to 4 workers (CPU count / 2).                                                                                                                                                                      |
| cleanup      | The server process tree is stopped on exit and on Ctrl+C. Server output is in `test-results/e2e-local-server.log`.                                                                                                                                                        |

Why a production build and not `next dev`: dev compiles each route on first request (40-58 s for `/` and `/login` cold), which broke the one-time login and caused hydration timeouts under parallel workers. A production build has no per-route compile, so warming only primes the server.

## Expected timings

Measured on the dev machine (Windows 11, 4 workers), five specs (button-contrast, motion-hygiene, theme-contrast, display-font, touch-targets; 27 tests):

| Scenario                                                      | Total | Result                                                       |
| ------------------------------------------------------------- | ----- | ------------------------------------------------------------ |
| Before: plain Playwright with a cold `next dev`               | 203 s | 26 passed, 1 failed (hydration timeout)                      |
| Before: `npm ci --prefer-offline` in a new worktree (one-off) | 255 s | removed by the node_modules link                             |
| After: clean worktree, nothing installed or built             | 113 s | 27 passed (setup 4 s, build 73 s, serve+warm 6 s, test 29 s) |
| After: same code, build cached                                | 34 s  | 27 passed (build 0.3 s, serve+warm 4 s, test 28 s)           |

So a clean worktree went from about 4 min of install plus a flaky 3.4 min run to under 2 min, and a repeat run is about 30 s.

## Real catalogue data

The e2e server runs with `CATALOGUE_SOURCE=fixture` (set in `scripts/e2e-env.cjs`), so the homepage, catalogue,
offer-detail and alerts specs run on the 24 sample offers with no seeded backend. `catalogue-live.spec.ts` (the
journeys on real published offers) is skipped unless `E2E_LIVE_CATALOGUE=1`, which starts the server with
`CATALOGUE_SOURCE=api`. It needs a backend seeded with published offers (the dev seed command, FREEJ-20) on the
TEST database, plus `BACKEND_API_URL` and `BACKEND_INTERNAL_KEY` as below:

```bash
export E2E_LIVE_CATALOGUE=1 BACKEND_API_URL=http://127.0.0.1:8170 BACKEND_INTERNAL_KEY=<the same key>
export E2E_ENDED_OFFER_SLUG=<slug of a seeded offer that was published and has ended>   # optional, journey 5
npm run test:e2e:local -- src/__tests__/e2e/catalogue-live.spec.ts
```

Run the fixture specs without `E2E_LIVE_CATALOGUE`; they skip themselves when it is set. The live specs were
written in FREEJ-21 but have not been run (no seeded backend existed yet).

## Admin offers journey

`admin-offers.spec.ts` (draft, verify, publish, see it live, unpublish) needs the live catalogue flags above and an
admin session. Besides the Firebase test project it can run against a private database with a test-only identity
server and two throwaway session values: see [admin-offers.md](admin-offers.md#journey-4-playwright).

## Setup before the first run

Run it for user-facing changes and record the command and result in the PR ([workflow.md](workflow.md)).

**1. `nextjs-frontend/.env.e2e.local`** (gitignored; the script copies it from the main checkout into a worktree). It holds the dedicated Firebase **test** project `yourapp-test` (public web config plus the `FIREBASE_ADMIN_*` service account, added by the owner), Stripe **test-mode** keys and price ids (`STRIPE_PRICE_ID_PRO_YEARLY` is the discounted yearly test price), and the accounts written by `npm run test:setup` (`TEST_USER_*`, `TEST_ADMIN_*`). Details and the "never mix dev and test" checks are in [testing.md](testing.md#dedicated-firebase-test-project-and-test-database). Never paste values into chat, tickets or commits.

**2. A backend for the app to call.** The frontend only talks to FastAPI, so the specs need your own backend on free ports. Do not reuse another session's container or port.

```bash
# throwaway Postgres 17 on a free port (pick your own)
docker run -d --name my-e2e-pg -e POSTGRES_PASSWORD=pw -e POSTGRES_DB=my_e2e -p 127.0.0.1:55470:5432 postgres:17

cd fastapi_backend
export DATABASE_URL=postgresql+asyncpg://postgres:pw@127.0.0.1:55470/my_e2e ENVIRONMENT=local
export BACKEND_INTERNAL_KEY=<32+ random chars> RATE_LIMIT_EMAIL_KEY=<32+ random chars>
export TOTP_ENCRYPTION_KEY=<a Fernet key>                       # without it the 2FA re-authentication spec gets 503
export BILLING_RETURN_URL_ORIGINS=http://localhost:<E2E port>   # without it checkout fails (the port is printed by the script)
# plus FIREBASE_ADMIN_* and STRIPE_* from .env.e2e.local (the test project, same as the frontend)
uv run alembic upgrade head
uv run uvicorn app.main:app --port 8170
```

The backend must not inherit `UPSTASH_REDIS_REST_*` (`.env.e2e.local` sets them): with `ENVIRONMENT=local` it would still use Upstash and login answers 503. Unset them (`unset UPSTASH_REDIS_REST_URL UPSTASH_REDIS_REST_TOKEN`) in the backend shell, and never open the env file to check.

In the shell you run the tests from:

```bash
export BACKEND_API_URL=http://127.0.0.1:8170 BACKEND_INTERNAL_KEY=<the same key>
```

The admin specs need an admin grant in the backend database for the `TEST_ADMIN_EMAIL` account. The user row exists only after that account has logged in once (the first run does it), so grant it after the first run and run again. The SQL below does it for this container. The same grant also works with `uv run python -m commands.make_admin <TEST_ADMIN_EMAIL> --role super_admin` from `fastapi_backend/`, with `ENVIRONMENT=local` and `DATABASE_URL` pointing at this database (see [getting-started.md](getting-started.md#first-admin)):

```bash
docker exec my-e2e-pg psql -U postgres -d my_e2e -c "insert into admin_users(id,user_id,role,permissions) select 'adm_e2e', id, 'super_admin', '[\"*\"]'::jsonb from users where email = '<TEST_ADMIN_EMAIL>' on conflict do nothing"
```

Remove the container when done (`docker rm -f my-e2e-pg`). A spec that fails with a 403 (admin), a 503 (2FA) or a checkout that never opens usually means one of these three settings is missing.

**3. A server you start yourself (optional).** `E2E_REUSE_SERVER=1` makes plain Playwright (`npm run test:e2e`, `test:e2e:baseline`) reuse a server already on `E2E_PORT` instead of starting one, so a normal dev server is never tested by accident. Build with `npm run test:e2e:build`, start it with `node scripts/e2e-start.mjs`. `npm run test:e2e:local` does not need this: it builds, serves and stops its own server.

## Money-path specs (no Firebase)

`outbound.spec.ts` (anonymous browse -> offer -> tracked outbound click, plus the ended-offer path) and
`alerts-lifecycle.spec.ts` (alerts double opt-in: sign up, confirm, unsubscribe, repeat submissions) are the two journeys
whose regressions cost money (redirects, consent). They need **no login and no Firebase project**, so they run in their
own Playwright projects, `money-desktop` (Desktop Chrome) and `money-mobile` (Pixel 7 user agent at 390 x 844), which have
no `setup` dependency (`playwright.local.config.ts`). When every spec on the command line is one of these two (or
`E2E_PUBLIC=1`), `test:e2e:local` switches to a **public run** (`scripts/e2e-public-mode.cjs`): no `.env.e2e.local`
needed, placeholder Firebase values for the build (nothing signs in), the live catalogue (`CATALOGUE_SOURCE=api`), the
alerts form open (`ALERTS_FORM_LIVE=true`), and only those two projects. Write other Playwright options as `--name=value`.
The backend must be local (`BACKEND_API_URL` host `localhost`, `127.0.0.1` or `[::1]`, or only `BACKEND_PORT`); a run with
`--project`/`-p` is never a public run.

```bash
# 1. A backend on a TEST database you own (the name must end in _test), the fake email provider and the seed
cd fastapi_backend
export DATABASE_URL=postgresql+asyncpg://postgres:<password>@127.0.0.1:5433/<yourname>_test ENVIRONMENT=test
export BACKEND_INTERNAL_KEY=<32+ random chars> RATE_LIMIT_EMAIL_KEY=<32+ random chars> SUBSCRIBER_TOKEN_SECRET=<32+ random chars>
export EMAIL_PROVIDER=fake EMAIL_FAKE_OUTBOX=/tmp/freejoy-outbox.jsonl PUBLIC_SITE_URL=http://localhost:3271
: > /tmp/freejoy-outbox.jsonl
uv run alembic upgrade head && uv run python -m commands.seed_catalogue
uv run uvicorn app.main:app --port 8271 --no-access-log

# 2. The specs (nextjs-frontend/), same key as the backend
export BACKEND_API_URL=http://127.0.0.1:8271 BACKEND_INTERNAL_KEY=<the same key> E2E_PORT=3271
export E2E_EMAIL_OUTBOX=/tmp/freejoy-outbox.jsonl
export E2E_PSQL="docker exec freejoy-test-pg psql -U postgres -d <yourname>_test"   # optional: the database seam
npm run test:e2e:local -- outbound alerts-lifecycle
```

- **The database seam (`E2E_PSQL`).** The browser and the Next.js server never read the database, so a click row or a
  subscriber's status can only be checked from the test process. `E2E_PSQL` is a `psql` command up to and including the
  connection, split on spaces (`psql postgresql://...` works as well as `docker exec <container> psql -U ... -d ...`).
  `helpers/db-seam.ts` appends `-X -tA -F | -c <SQL>` and makes the session read-only (`PGOPTIONS=-c
  default_transaction_read_only=on`, passed with `-e` for `docker exec`). It **refuses** a connection whose database
  name (`-d`, `--dbname`, `dbname=` or a `postgres://` URL path) does not end in `_test`. Keep the password out of the
  command line: use `PGPASSWORD` (or `~/.pgpass`) with `psql -h ... -U ... -d ...`. With it, the specs
  assert that exactly one `click_events` row (device class and referrer host only) is written for the click, that an
  ended offer writes none, and each subscriber status along the lifecycle (`PENDING`, `CONFIRMED`, `UNSUBSCRIBED`, and
  that an unsubscribed address stays `UNSUBSCRIBED` after a new submission). Without it those assertions are skipped
  (the report notes it) and everything else still runs.
- **The ended offer.** `/go/<id>` needs the offer's id. The spec reads `GO_E2E_ENDED_OFFER_ID` (the variable
  `go-redirect.spec.ts` uses) or, with the seam, looks `laundry-sheets-sample` up (`E2E_ENDED_OFFER_SLUG` overrides the
  slug); with neither, that test skips. The seeded ended offer is dated relative to the day `seed_catalogue` ran, and its
  page is a 404 once it has been over for 30 days: re-run the seed command if the spec says so.
- **Rows and addresses.** Every run adds click rows and subscriber rows to the test database (nothing deletes them) and
  every address is new for the run, so repeat runs are safe; the backend's limiter is off with `ENVIRONMENT=test`.
- **Nothing leaves the machine.** The browser in `outbound.spec.ts` is started with every host except localhost
  unresolvable (`helpers/block-external-hosts.ts`), so the real `307` is followed in a real browser and the request to
  the provider dies at name resolution (the spec asserts `ERR_NAME_NOT_RESOLVED`). `page.route()` cannot stub a redirect
  target in Chromium, which is why the block is done at the resolver. Known gap: the two click-through tests in
  `go-redirect.spec.ts` stub the provider with `page.route()`, so on a redirect they do reach the real provider origin
  (`example.com` with the sample seed). Moving them to the same block is a follow-up.
- **Redirect status.** The redirect to the provider is a `307` (the story text says 302): the spec asserts what
  `src/lib/outbound/respond.ts` does. The ended-offer redirect is a relative `302`.
- **CI.** `.github/workflows/e2e.yml` has a `money-paths` job with the whole recipe (Postgres service, backend, fake
  provider), behind the same `E2E_ENABLED` switch as the Firebase job, so it is off until the owner turns E2E on. It has
  not been run on GitHub.

## Alerts sign-up (fake email provider)

`alerts-lifecycle.spec.ts` (see [Money-path specs](#money-path-specs-no-firebase), which is the quickest way to run it) runs the
double opt-in in a browser without sending any email. It needs the backend to use the fake provider and the frontend to be
served with the form open. The same fake-provider variables also drive it in a full, Firebase-backed run:

```bash
# backend shell (local DB, ENVIRONMENT=local as above); the fake provider is refused anywhere else
export EMAIL_PROVIDER=fake EMAIL_FAKE_OUTBOX=/tmp/freejoy-outbox.jsonl
export SUBSCRIBER_TOKEN_SECRET=<32+ random chars> RATE_LIMIT_EMAIL_KEY=<32+ random chars>
export PUBLIC_SITE_URL=http://localhost:<E2E port>
: > /tmp/freejoy-outbox.jsonl
# frontend shell: start the server with the form open, and tell the spec where the outbox is
export ALERTS_FORM_LIVE=true E2E_ALERTS_LIVE=1 E2E_EMAIL_OUTBOX=/tmp/freejoy-outbox.jsonl
npm run test:e2e:local -- src/__tests__/e2e/alerts.spec.ts src/__tests__/e2e/alerts-lifecycle.spec.ts
```

The spec reads the confirm and unsubscribe links from the outbox file, the way a person reads their inbox. Without
`E2E_EMAIL_OUTBOX` and `E2E_ALERTS_LIVE=1` the opt-in spec and the form tests in `alerts.spec.ts` skip, and the
closed-page tests run instead. `alerts-token-pages.spec.ts` needs neither a backend nor an outbox. The page reads
`ALERTS_FORM_LIVE` when the server starts, so restart the server (not rebuild) to switch between open and closed runs.

## Plan prices (`E2E_PLAN_PRICES=1`)

The pricing page shows Pro and Enterprise amounts that the Next.js server reads from the backend (`GET /v1/settings/plan-prices`, minor units, see [backend-frontend-seams.md](backend-frontend-seams.md#get-v1settingsplan-prices)) and the currency from `GET /v1/settings/currency`. A backend that cannot read them (no Stripe test price ids, or no backend at all) makes every paid tier show 0, so the price-dependent tests in `pricing.spec.ts` are off unless `E2E_PLAN_PRICES=1`:

- `Pricing Tiers > should display prices for each tier`, `Billing Period Toggle > should show the savings badge only when yearly billing is cheaper`, both `Price Display` groups and `Pricing Accuracy`. Without the flag they are reported as skipped ("Needs a backend that returns plan prices (E2E_PLAN_PRICES=1)"). The toggle tests (role, `aria-checked`, roving tabindex, arrow/Home/End keys) never need it.
- **What the backend must provide:** a plan-prices route that answers **non-zero** amounts for Pro and Enterprise, monthly and yearly (the four `STRIPE_PRICE_ID_{PRO,ENTERPRISE}_{MONTHLY,YEARLY}` test prices from `.env.e2e.local` with a `STRIPE_SECRET_KEY` for the test account), plus a currency. Zero or `null` amounts fail the tests on purpose.
- **Why it cannot be stubbed in the test:** the browser never calls FastAPI; the server component calls it, so `page.route()` cannot intercept the read. Point `BACKEND_API_URL` at a backend (or a local stand-in for the two routes) that returns the prices. The server caches a successful read for an hour, so restart `next start` after changing prices.
- **Where it runs:** the spec is part of the Firebase-backed `chromium` project (it depends on the `setup` login project); it is not one of the no-login money-path projects, so a public run does not select it.
- Independent of `E2E_STRIPE`: no webhook forwarding is needed.

```bash
export E2E_PLAN_PRICES=1 BACKEND_API_URL=http://127.0.0.1:8170 BACKEND_INTERNAL_KEY=<the same key>
npm run test:e2e:local -- src/__tests__/e2e/pricing.spec.ts
```

## Stripe webhooks

Stripe cannot reach `localhost`, so a checkout completes at Stripe but the app never learns it ("Processing Your Subscription..." forever). The backend receives the events at `POST /webhooks/stripe` and verifies each one with `STRIPE_WEBHOOK_SECRET`. For e2e the Stripe CLI forwards test-mode events to your local uvicorn. The Stripe specs run only with `E2E_STRIPE=1` (`E2E_ENABLED` is unrelated and stays `false`).

**Locally**, after the backend from step 2 above is up, in a separate terminal from `nextjs-frontend/`:

```bash
export BACKEND_API_URL=http://127.0.0.1:8170          # your uvicorn
node scripts/e2e-stripe-listen.mjs                   # = stripe listen --forward-to $BACKEND_API_URL/webhooks/stripe
```

- The script runs the Stripe CLI with its own login (`stripe login`, test mode) and reads no env files. The CLI login expires (about 90 days): run `stripe login` again if `stripe listen` answers `api_key_expired`.
- **The backend must run with the secret `stripe listen` prints.** `.env.e2e.local` holds it as `STRIPE_WEBHOOK_SECRET` (the same variable the backend reads); it is stable for one Stripe account, so set it once. To check or refresh it: `node scripts/e2e-stripe-listen.mjs --print-secret` prints it (do not paste it anywhere). If the backend has a different secret, every event is answered `400` and the test times out.
- Start the backend with the `STRIPE_*` variables from `.env.e2e.local`, including the four `STRIPE_PRICE_ID_{PRO,ENTERPRISE}_{MONTHLY,YEARLY}` names. Keep the forwarder running while the tests run; its log shows each event and the backend's status.

Then run the spec with the flag in the test shell:

```bash
E2E_STRIPE=1 npm run test:e2e:local -- src/__tests__/e2e/subscription-simple.spec.ts
```

**In CI** (`.github/workflows/e2e.yml`, still switched off by `E2E_ENABLED`): when the repository variable `E2E_STRIPE` is `true`, the job installs a pinned Stripe CLI (checksum verified), runs `stripe listen --print-secret` and exports the result as `STRIPE_WEBHOOK_SECRET` (masked), forwards events to `BACKEND_API_URL` (variable `E2E_BACKEND_API_URL`, default `http://127.0.0.1:8000`) and sets `E2E_STRIPE=1` for the Playwright step. It uses the test-mode key `E2E_STRIPE_SECRET_KEY` from the `e2e-test` environment, which the job's check step already restricts to `sk_test_`/`rk_test_`. The job does not start a backend yet; when one is added it must start after the forwarding step so it picks up the exported secret. Alternative if forwarding from the runner is ever unsuitable: register a test-mode webhook endpoint on a public deployed test backend and give the backend that endpoint's signing secret.

## Focused specs vs the full run

Measured on `main` 1a4c9e2 (Windows 11, 4 workers), against a fresh throwaway backend:

| What                                                     | Command                                                         | Time                           |
| -------------------------------------------------------- | --------------------------------------------------------------- | ------------------------------ |
| Focused: the spec(s) of the area you changed             | `npm run test:e2e:local -- src/__tests__/e2e/dashboard.spec.ts` | build cached: under 1 min      |
| Four specs (admin, billing, reauth, upgrade)             | `npm run test:e2e:local -- <four specs>`                        | 170 s (60 tests, cached build) |
| Full suite, cold build (255 tests: 235 pass, 20 skipped) | `npm run test:e2e:local`                                        | 214 s (build 54 s, test 152 s) |

Run the full suite for broad changes or anything touching auth, the proxy or shared layouts.

## Quarantined tests

A test that fails on a clean `main` is quarantined, never skipped silently: `test.fixme(...)` with the issue reference in a comment beside it. Remove the `.fixme` in the PR that fixes it. Skipped by default:

| Test                                                                                                  | Spec                                 | Ticket                                                    | Why                                                                                                                                                                                                |
| ----------------------------------------------------------------------------------------------------- | ------------------------------------ | --------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Stripe Checkout upgrade test (skipped unless `E2E_STRIPE=1`)                                          | `subscription-simple.spec`           |  | Needs Stripe Checkout and the webhook forwarded to the backend: see [Stripe webhooks](#stripe-webhooks)                                                                                    |
| Stripe Checkout, test cards, auto-renewal toggle and saved-card tests (skipped unless `E2E_STRIPE=1`) | `subscription`, `billing-management` |  | Skipped by default. With `E2E_STRIPE=1` and the webhook forwarded they still need a real Stripe subscription or card, which an admin grant does not create (revived only the upgrade test) |
| 2FA section and authenticator enrolment (skipped)                                                     | `dashboard`, `settings-totp`         |                                                   | The setup screen is hidden on purpose (no backup codes yet); sign-in itself asks for the code                                                                                                                                                     |
| Data export section (skipped)                                                                         | `dashboard`                          |                                                   | Switched off until                                                                                                                                                                         |


## Seeding an organisation on Pro

`loginAsThrowawayAccountOnPro` (`helpers/e2e-utils.ts`) gives a throwaway account an organisation on the Pro plan without Stripe: the saved admin session calls the admin grant API (`PUT /v1/admin/subscriptions/{id}/grant`). It needs the saved admin session (`auth.setup.ts`), an admin account with the `subscriptions.grant` permission (the `super_admin` insert above gives it) and `BACKEND_INTERNAL_KEY` in the test shell; without the key the tests fail rather than skip. A grant has no Stripe subscription, so use it for plan-display tests only (pricing "Current Plan"), not for auto-renewal or saved cards.

## Ports

Each checkout gets a stable port in 3100-3899 derived from its path; the script walks forward if it is busy. The build bakes the port in (`NEXT_PUBLIC_APP_URL`), so a different port forces a rebuild, which is why the default is stable. Set `E2E_PORT=3177` to choose one yourself (the script stops if it is taken).

## Troubleshooting

- **`.env.e2e.local is missing`**: copied from the main checkout when it exists there. Otherwise create it as described in [testing.md](testing.md) (yourapp-test Firebase web config, `FIREBASE_ADMIN_*`, accounts from `npm run test:setup`). It is gitignored; never commit it.
- **`E2E_PORT=... is already in use`**: unset `E2E_PORT`, or stop the process (`npm run dev:kill` clears stuck dev servers).
- **Server did not start (cold or crashed)**: read `test-results/e2e-local-server.log`. `scripts/e2e-start.mjs` refuses a build made for another Firebase project or port; delete `.next-e2e` to force a rebuild.
- **Tests time out in `page.goto`**: too many workers for the machine; add `--workers=2`.
- **Stale build**: any change to tracked or untracked files triggers a rebuild automatically; delete `.next-e2e` to force one.
- **Shared `node_modules`**: it is a link to the main checkout's. Running `npm ci` in the main checkout changes it for every worktree. Set `E2E_LOCAL_NO_LINK=1` for your own copy. If the lockfile later differs from the main checkout's, the script removes only the link and runs `npm ci`.
- **Deleting a worktree**: it holds a link to the main checkout's `node_modules`. Remove the link first (`rmdir node_modules` in Windows `cmd`, `rm node_modules` elsewhere, no recursive flag), then remove the worktree. A recursive delete that follows the link would destroy the main checkout's dependencies.
- **Plain `npm run test:e2e`** still works and starts `next dev`. The setup project now allows 90 s and the post-login redirect 45 s, so a cold dev compile no longer fails the login, but the prebuilt run is much faster.

## Not covered

This does not enable E2E in CI (`E2E_ENABLED` stays `false`) and does not change what any test asserts. Specs that need `TEST_ADMIN_EMAIL`/`TEST_USER_EMAIL` skip until `npm run test:setup` has created the accounts. The optional nightly Playwright workflow is a separate story.
