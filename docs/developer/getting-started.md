# Getting started

## Prerequisites

| Tool | Version | Notes |
| --- | --- | --- |
| Node.js | 22.12 or later (22 or 24) | `nextjs-frontend/package.json` declares `engines.node`. |
| npm | 10 or later | npm only. Do not use pnpm or yarn. |
| Python | 3.12 | Required by `fastapi_backend/pyproject.toml` (`>=3.12,<3.13`). |
| uv | current (CI uses 0.8.12) | Manages the backend environment and dependencies. |
| Docker | current | For Postgres (and optionally the whole stack) through `docker-compose.yml`. A local Postgres 16 works too. |
| Firebase project | | Authentication with Email/Password (and Google if you want it) enabled; a service account key for the Admin SDK. |

## Clone

```bash
git clone https://github.com/<you>/<your-repo>.git
cd <your-repo>
```

Two apps: `nextjs-frontend/` and `fastapi_backend/`. Each has its own setup below.

## Database

```bash
docker compose up -d db          # Postgres 16 on localhost:5432, database "yourapp"
docker compose up -d test_db     # optional: a second instance on 5434 for the Postgres-backed backend tests
```

The compose stack uses the fake local credentials from the root `.env.example` (`postgres` / `password`); copy it
to `.env` only to change ports or the compose project name. Without Docker, create a database on your own
Postgres and point `DATABASE_URL` at it.

Most backend tests need no database (they use fake sessions). The `*_e2e.py` tests run only when
`TEST_DATABASE_URL` points at a local database whose name ends in `_test`; see [testing.md](testing.md).

## Backend (`fastapi_backend/`)

```bash
cd fastapi_backend
uv sync
cp .env.example .env             # fill in the Firebase Admin values; set BACKEND_INTERNAL_KEY to any random string
uv run alembic upgrade head
uv run fastapi dev app/main.py --host 127.0.0.1 --port 8000 --reload
```

`app/core/config.py` reads `.env` from the working directory; every variable is documented in
`fastapi_backend/.env.example`, which is the reference. The test suite, the OpenAPI schema commands and
`scripts/gate.sh` deliberately do not read it ([testing.md](testing.md#local-env-and-the-test-suite)). In short:

| Variable | Required | Notes |
| --- | --- | --- |
| `DATABASE_URL` | Yes | asyncpg URL of the platform database. `DATABASE_URL_DIRECT` (optional) is used by Alembic when the runtime URL goes through a pooler. |
| `ENVIRONMENT` | No | `local` (default in the example), `test` or `production`. Production enforces the "required outside local/test" rows. |
| `BACKEND_INTERNAL_KEY` | Outside local/test | Shared service key the Next.js server sends as `X-Internal-Key`. `BACKEND_INTERNAL_KEY_NEXT` allows rotation. |
| `FIREBASE_ADMIN_PROJECT_ID`, `FIREBASE_ADMIN_CLIENT_EMAIL`, `FIREBASE_ADMIN_PRIVATE_KEY` | Yes, to sign in | ID token and session cookie verification. |
| `UPSTASH_REDIS_REST_URL`, `UPSTASH_REDIS_REST_TOKEN`, `RATE_LIMIT_EMAIL_KEY` | Outside local/test | Rate limiting (ADR-007). Unset locally: the limiter is disabled. |
| `TOTP_ENCRYPTION_KEY` | To enable 2FA | Fernet key for TOTP secrets at rest. |
| `SUBSCRIBER_TOKEN_SECRET` | Outside local/test | 32+ character HMAC secret for the alerts confirm and unsubscribe tokens. |
| `EMAIL_PROVIDER`, `EMAIL_PROVIDER_API_KEY`, `EMAIL_PROVIDER_LIST_ID`, `EMAIL_FROM`, `EMAIL_FROM_NAME`, `PUBLIC_SITE_URL` | To send alerts email | Backend only ([ADR-012](../adr/0012-email-provider.md), [backend-subscribers.md](backend-subscribers.md)). `EMAIL_PROVIDER` unset: nothing is ever sent, and production refuses sign-ups. `fake` records messages for tests (local/test only). |
| `SENTRY_DSN` | No | Unset disables Sentry. |
| `APP_NAME`, `TOTP_ISSUER`, `LOG_LEVEL` | No | Brand (API title), the name shown in the authenticator app (keep equal to `NEXT_PUBLIC_APP_NAME`), and log level. |
| `STRIPE_*`, `BILLING_RETURN_URL_ORIGINS` | Billing module | See [modules.md](modules.md). |

Check it with `curl http://localhost:8000/health` (`{"status":"ok"}`); `/health/ready` also checks the database and
pending migrations and returns 503 until `alembic upgrade head` has run. `/docs`, `/redoc` and `/openapi.json`
exist only when `ENVIRONMENT` is `local` or `test` (or a Vercel preview); in production they return 404.

`./start.sh` runs the API together with `watcher.py`, which regenerates the OpenAPI file on changes (on Windows,
run it from Git Bash). To regenerate once: `uv run python -m commands.generate_openapi_schema`.

## Sample catalogue data

A local database has no categories, merchants or offers until you seed it. After `uv run alembic upgrade head`, run
this from `fastapi_backend/`:

```bash
uv run python -m commands.seed_catalogue
```

It loads the same 6 categories, 8 merchants and 24 offers as the Phase 1 fixtures
(`nextjs-frontend/src/lib/catalogue/fixtures.ts`) plus one affiliate link per offer, then prints how many rows are in
place. The rules:

- **Local and test only.** It runs only when `ENVIRONMENT` is exactly `local` or `test`. Any other value, an empty
  one, an unset one (the setting then defaults to `production`) or a configuration that cannot be loaded makes it
  print a refusal, write nothing and exit with code 1. The `DATABASE_URL` host must also be `localhost`, `127.0.0.1` or
  `::1`: a remote host (even a `*_test` database), a unix socket, a `host=` query parameter, an empty or unparsable
  URL all refuse the same way. It never prints the URL, the host or the environment value.
  The guard checks the host name in the URL, not where the port leads: an SSH tunnel or port-forward to `localhost`
  passes.
- **Obviously sample data.** Every offer has `publisher_notes = "SAMPLE DATA — not a real offer"`, merchant names end
  in `(Sample)`, merchants have no logo, and every website, source and affiliate destination is under
  `https://example.com/`. No real brand, logo or destination is used.
- **Every state.** All five offer types and all four statuses are present: 21 published, 1 draft (`cat-pouch-tasting-sample`),
  1 expired (`laundry-sheets-sample`) and 1 archived. The ended Bedtime hamper prize draw is archived here (the fixtures list it as expired) so
  admin screens have an archived offer to work with. Links of expired and archived offers are inactive.
- **Safe to rerun.** It upserts merchants and offers by `slug` (categories are insert-only) and keeps one `sample` affiliate link per
  offer, so a second run creates no rows and a changed row is put back to the seed values. Rows with other slugs are
  left alone; a merchant or offer with a seed slug that is not sample data makes the run refuse and write nothing;
  existing categories are never modified. Click events and email subscribers are never seeded.
- **Dates move with the day you run it.** Dates are offsets from today (the fixtures use 2026-10-29), so published
  offers stay live and carry a "last checked" date that is never in the future. The closing dates in the
  availability text are filled in from the same offsets, so they match `end_at`. Running it on a later day moves
  everything forward.

Run `uv run alembic upgrade head` first: on an unmigrated database it reports the failure (error type only) and exits 1.
Tests: `tests/commands/test_seed_catalogue.py` (the guards and the data need no database; the upsert tests run with
`TEST_DATABASE_URL`, see [testing.md](testing.md)) and `test_seed_catalogue_upsert.py` (upsert rules on a fake session).

## Frontend (`nextjs-frontend/`)

```bash
cd nextjs-frontend
npm ci
cp .env.example .env.local       # fill in the Firebase client and Admin values; BACKEND_INTERNAL_KEY must equal the backend's
npm run typecheck
npm run dev                      # http://localhost:3000
```

`nextjs-frontend/.env.example` documents every variable. The groups:

| Group | Variables | Notes |
| --- | --- | --- |
| Backend | `BACKEND_API_URL`, `BACKEND_INTERNAL_KEY` | Server-only. Locally the FastAPI dev server URL; on Vercel the service binding injects `BACKEND_API_URL`. |
| Firebase client | `NEXT_PUBLIC_FIREBASE_*` | Public config from the Firebase console. CI builds with placeholders. |
| Firebase Admin | `FIREBASE_ADMIN_*` | Server-only, session cookie verification. |
| App | `NEXT_PUBLIC_APP_URL`, `NEXT_PUBLIC_APP_NAME`, `NEXT_PUBLIC_SUPPORT_EMAIL`, `LOG_LEVEL` | Brand strings are read by `src/config/site.ts`. |
| Monitoring | `NEXT_PUBLIC_SENTRY_DSN`, `SENTRY_AUTH_TOKEN` | Optional. |
| Ports | `PORT`, `E2E_PORT`, `BACKEND_PORT` | Optional, for parallel checkouts (below). |
| Stripe Billing module | `NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY`, `STRIPE_PRICE_ID_*` | See [modules.md](modules.md). |
| Capacitor module | `APP_ID`, `APP_NAME` | See [modules.md](modules.md). |

The frontend has no database and no server secrets other than the Firebase Admin credentials and the internal
key: sessions, ID tokens, password changes and plan prices all go through the backend. Stripe return URLs are
built on the server from `NEXT_PUBLIC_APP_URL` (required in production; Vercel previews fall back to
`https://$VERCEL_URL`, local to `http://localhost:3000`).

`npm run build` needs only the `NEXT_PUBLIC_*` values (placeholders are fine, as in CI). The `predev` hook runs
`scripts/dev-clean.cjs`; if a stale lock blocks startup, `npm run dev:kill` then `npm run dev`.

## First admin

Admin pages need an admin grant in the backend. The first admin is granted with a command, run by hand from
`fastapi_backend/`. Look first, then grant:

```bash
uv run python -m commands.make_admin someone@example.com --dry-run   # report only, change nothing
uv run python -m commands.make_admin someone@example.com             # grant the admin role
uv run python -m commands.make_admin someone@example.com --role super_admin
```

- **Environment.** The command imports the app settings, so the environment it runs in must be valid for the app:
  `ENVIRONMENT=local` (or `test`) with a `DATABASE_URL` for local work, or the full production configuration
  (`ENVIRONMENT` unset means production, which also needs the Upstash, Firebase Admin and internal key values) to
  grant on a deployed database. It uses `DATABASE_URL`, the app's connection; unlike Alembic it does not prefer
  `DATABASE_URL_DIRECT`. The first line it prints names the environment and the database (`host:port/name`, never the
  password), so check that before reading the rest.
- **Who.** The person must have signed in once: users are created by their first Firebase sign-in and the command never
  creates one. The email is trimmed and matched ignoring ASCII case; no match prints "no user found" and exits 1.
  Only plain printable ASCII emails are accepted (an accented or look-alike letter is exit 2; grant such an account from
  the admin Users page, which works by user id). Every run
  prints the matched user's id, stored email, `email_verified`, `created_at` and `last_login_at` (that user only), which
  is why `--dry-run` first is the recommended habit (the email is shown escaped, so look-alike and control
  characters are visible). A user whose email is not verified is refused when a change would be made, unless
  `--allow-unverified` is passed; a no-op on an unverified account succeeds with a warning line.
- **Idempotent.** A user who already holds the role is left alone. A `super_admin` covers the default `admin` role
  (no change) only when its list contains `*`; a hollow `super_admin` (no `*`) on the default role is refused, and is
  repaired with `--role super_admin` or changed to `admin` with `--allow-demote`. Changing any `super_admin` to
  `admin` needs `--allow-demote`, and the last super admin is never demoted. An `admin` holding fewer
  than all permissions is reported ("N of M") and kept as is; `--refresh-permissions` grants the full set. Hollow rows
  (a `super_admin` whose list is not exactly `["*"]`, or an `admin` with no usable permission) are repaired.

After that an existing admin grants others through `PUT /v1/admin/users/{user_id}/admin` (the Users page in the admin
area). Details, the permissions it writes and the audit row: [backend-admin-users.md](backend-admin-users.md#bootstrapping-the-first-admin).

## The whole stack in Docker

```bash
docker compose up                # frontend :3000, backend :8000, Postgres :5432 and :5434, Redis :6379
scripts/compose-smoke.sh all     # what CI runs: health checks, then the degraded case, then down
```

The compose stack uses fake local values and no secrets, so sign-in does not work in it without your Firebase
values in `.env`; it exists to prove the images build and the apps start.

## Pre-commit hooks

```bash
uvx pre-commit install           # once per clone, after both apps are set up
uvx pre-commit run --from-ref origin/main --to-ref HEAD
```

Runs ruff, ruff format and mypy on `fastapi_backend/`, ESLint, Prettier and `tsc --noEmit` on `nextjs-frontend/`,
plus the OpenAPI client and `requirements.txt` sync checks.

## Running in parallel

Two checkouts (for example two git worktrees) can run side by side when each uses its own ports. Defaults are
unchanged; CI and Vercel set none of these.

| Variable | Default | What it moves |
| --- | --- | --- |
| `PORT` | `3000` | `npm run dev`, `npm start`, `npm run dev:kill` (kills only the listener on exactly this port) |
| `E2E_PORT` | `3000` | The Playwright-managed server, `baseURL`, and the e2e build (the URL is baked in at build time) |
| `BACKEND_PORT` | `8000` | `fastapi_backend/start.sh`, and the e2e server's `BACKEND_API_URL` (`http://127.0.0.1:$BACKEND_PORT`) |
| `BACKEND_API_URL` | unset | Wins over `BACKEND_PORT` when set explicitly |
| `COMPOSE_PROJECT_NAME`, `*_PORT` in root `.env` | `yourapp`, 5432, 5434, 6379, 8000, 3000 | Docker stack: separate containers, volumes and host ports |

```bash
# fastapi_backend/ (terminal 1)
BACKEND_PORT=8100 ./start.sh
# nextjs-frontend/ (terminal 2)
PORT=3100 BACKEND_API_URL=http://127.0.0.1:8100 npm run dev
# nextjs-frontend/: e2e (the build records its port; a build for another E2E_PORT is refused)
E2E_PORT=3100 BACKEND_PORT=8100 npm run test:e2e:build
```

## Troubleshooting

- **Stuck dev server or `.next/dev/lock` error:** `npm run dev:kill`, then `npm run dev`.
- **`Module not found` after switching package manager:** npm only. Delete `node_modules` and run `npm ci`.
- **`Can't reach database server`:** check `DATABASE_URL`, that Postgres is running, and the port.
- **`/health/ready` returns 503 `migrations_pending`:** run `uv run alembic upgrade head`.
- **`Firebase: Error (auth/invalid-api-key)`:** the `NEXT_PUBLIC_FIREBASE_*` values must match the Firebase console, and Email/Password sign-in must be enabled there.
- **Sign-in succeeds in Firebase but the app returns to the login page:** `BACKEND_INTERNAL_KEY` differs between the two apps, or the backend's Firebase Admin credentials are for another project.
- **Stripe webhook signature failures locally:** `stripe listen --forward-to localhost:8000/webhooks/stripe` and set `STRIPE_WEBHOOK_SECRET` to the secret it prints (billing module only).

Next: [workflow.md](workflow.md) for the development loop, [testing.md](testing.md) for the test commands,
[architecture.md](architecture.md) for the request flow.
