# Testing

Coverage target: **80%** minimum. Write the test first (RED), make it pass (GREEN), then refactor.

## Frontend unit tests (Vitest)

```bash
cd nextjs-frontend
npm run test:unit          # src/__tests__/unit
npm run test:watch         # watch mode
npm run test:coverage      # enforces the coverage ratchet (see below)
```

- Config: `vitest.config.ts`. Environment is jsdom, setup file is `vitest.setup.ts`.
- **Coverage ratchet.** The thresholds in `vitest.config.ts` are a ratchet: CI fails when coverage drops below them. They are set about 2 points under the measured coverage (statements 90, branches 88, functions 88, lines 91 against 91.5 / 90.3 / 89.7 / 92.3 measured); the project minimum is 80%. Raise them when measured coverage rises and never lower them, except after a tooling change that alters how coverage is measured. To raise them to the current values in one step, run `COVERAGE_RATCHET=1 npm run test:coverage`, then `npx prettier --write vitest.config.ts`, lower each number by about 2 points to leave a margin, and commit the config.
- Coverage counts `src/lib/**`, `src/components/**` and `src/app/actions/**`.
- Vitest excludes `e2e/` and `*.spec.ts`.

## Frontend integration tests (Vitest)

There is no `test:integration` script yet (removed it, because no integration tests exist). Add the script and a `src/__tests__/integration` folder together with the first real test that exercises an API endpoint or a database.

## Frontend end-to-end tests (Playwright)

```bash
npm run test:setup                 # once: create the e2e user and admin in the TEST Firebase project
npm run test:e2e:build             # build the e2e app once (.next-e2e, test Firebase project, Sentry off)
npm run test:e2e:baseline          # the gated Chromium baseline against that build (never builds)
npm run test:e2e:baseline:build    # both of the above
npm run test:e2e                   # all projects in playwright.config.ts, against `npm run dev`
npm run test:e2e:ui                # interactive UI mode
```

- Tests live in `nextjs-frontend/src/__tests__/e2e/`. `auth.setup.ts` runs first, and the browser projects depend on it.
- **The baseline** (`playwright.baseline.config.ts`) is Chromium only and runs the core flows: auth, session-expiry, dashboard, subscription, subscription-simple, billing-management and admin. homepage, currency and pricing specs stay in the repo outside the gate. It uses 4 workers locally and 1 in CI.
- Specs import `test` and `expect` from `helpers/fixtures.ts`, not from `@playwright/test`.

### Dedicated Firebase test project and test database

e2e runs against the Firebase TEST project **`yourapp-test`** and a **dedicated Postgres test database**, never the dev project or dev database in `.env.local`/`.env`. **The dev and test environments must never be mixed:** the scripts refuse to run when the e2e config is missing, names the dev project, pairs a service account from one project with the web config of another, or when the e2e `DATABASE_URL` is missing or equals the dev one (they compare the parsed values and print only "same"/"different" or the host).

- All e2e config lives in `nextjs-frontend/.env.e2e.local` (gitignored). `scripts/e2e-env.cjs` merges it over `.env.local` and `.env` for e2e only. The Firebase variables and `DATABASE_URL` are taken only from `.env.e2e.local` or real environment variables (CI), never from `.env`/`.env.local`. Your normal `.env.local` stays untouched.
- `.env.e2e.local` holds:
  - the test project's public web config: `NEXT_PUBLIC_FIREBASE_API_KEY`, `NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN`, `NEXT_PUBLIC_FIREBASE_PROJECT_ID`, `NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET`, `NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID`, `NEXT_PUBLIC_FIREBASE_APP_ID`;
  - the test project's service account, which **the project owner adds by hand**: `FIREBASE_ADMIN_PROJECT_ID`, `FIREBASE_ADMIN_CLIENT_EMAIL`, `FIREBASE_ADMIN_PRIVATE_KEY` (Firebase Console > Project settings > Service accounts > Generate new private key, for `yourapp-test`). Never paste these into chat, tickets or commits;
  - `DATABASE_URL`, the dedicated e2e test database, added by the project owner (never the dev database);
  - the e2e accounts, written by `npm run test:setup`: `TEST_USER_EMAIL`, `TEST_USER_PASSWORD`, `TEST_ADMIN_EMAIL`, `TEST_ADMIN_PASSWORD`.
- In the test project, Firebase Console > Authentication > Sign-in method must have **Email/Password** enabled. `auth.setup.ts` fails with a clear message if it is not.
- `npm run test:setup`, `auth.setup.ts` and `scripts/e2e-start.mjs` fail fast and name the missing variables. They never print values.
- The frontend has no database of its own: the backend creates each e2e user's row on the first login, and the admin needs an admin grant in the backend database ([local-e2e.md](local-e2e.md)).

### Separate e2e build

`NEXT_PUBLIC_*` values are baked in at build time, so the e2e build must be separate from your normal local build:

- `npm run test:e2e:build` builds into **`.next-e2e`** (not `.next`) with the test project's config and an empty `NEXT_PUBLIC_SENTRY_DSN` (Sentry off). It writes `.next-e2e/e2e-build.json` with the Firebase project id.
- The baseline's webServer runs `scripts/e2e-start.mjs`, which only serves. It refuses to start when there is no e2e build, when the build's project id differs from the current e2e env, or when the build had Sentry on. It warns when the build is from another commit.
- **Ports:** the e2e server uses `E2E_PORT` (default 3000) and reaches the backend at `BACKEND_PORT` (default 8000) unless `BACKEND_API_URL` is set. The build marker records the port, because `NEXT_PUBLIC_APP_URL` is baked in at build time; `e2e-start.mjs` refuses a build made for a different `E2E_PORT`. Tests read the origin from `E2E_BASE_URL`, which `playwright.config.ts` sets. See [Running in parallel](getting-started.md#running-in-parallel).
- Playwright reuses a server already on the e2e port (`E2E_PORT`, default 3000) only with `E2E_REUSE_SERVER=1`, so a normal dev server (dev project) is never tested by accident. To keep one running between runs, start it with `node scripts/e2e-start.mjs`.
- CI builds in its own step (`npm run test:e2e:build`), then runs `npm run test:e2e:baseline`.
- `npm run test:e2e` (the full config) starts `npm run dev` with the e2e env and its own output folder `.next-e2e-dev`.
- **Test rate-limit identities:** the E2E scripts explicitly enable `E2E_RATE_LIMIT_TEST_MODE=1` for the dedicated `.next-e2e` / `.next-e2e-dev` artifact. `useDistinctClientIp` sends `x-e2e-client-ip` only to the local app, preserving the real authentication limits while isolating test buckets. Ordinary builds compile this override off; Vercel deployments always reject it, even for an E2E artifact. Rebuild older E2E artifacts before running these tests. Outside Vercel, ordinary requests share a conservative bucket rather than trusting forwarded headers. This is an identifier fix; durable serverless limits remain .

### Logins, sessions and test accounts

- `auth.setup.ts` logs in through the UI **once per role** (`user`, `admin`) and saves the session cookies to `playwright/.auth/<role>.json` (gitignored). Read-only specs use `test.use({ storageState: STORAGE_STATE.<role> })`, or `signInWithSavedSession(context, role)` for one test in an otherwise logged-out describe.
- `loginViaUI` stays only where the login itself is under test, and for throwaway accounts.
- **Isolation:** a test that logs out, revokes or expires a session, or changes plan or billing state (Stripe checkout, auto-renewal) must use the `throwawayAccount` fixture: a fresh free-tier account created in the test project and the e2e database, logged in with `loginAsThrowawayAccount`, and deleted after the test. Firebase revokes sessions per user, not per cookie, so one such test on a shared role account would break every other worker's saved session.
- No `networkidle` waits. Firebase, Stripe and Sentry keep connections open, so `networkidle` can time out on its own. `waitForPageReady` waits for `load` and React hydration. Use web-first assertions, `waitForURL`, or `waitForServerActionResponse`.
- Every browser context blocks `*.sentry.io` (in `helpers/fixtures.ts`), in addition to the Sentry-off build.

### Test accounts

- `npm run test:setup` runs `scripts/setup-e2e-accounts.mjs`. It:
  - creates one e2e user and one admin in the **test** Firebase project through the Identity Toolkit admin REST API (`scripts/firebase-rest-admin.mjs`, no `firebase-admin` package), using the e2e env only;
  - updates only the `TEST_*` keys in `.env.e2e.local` and keeps everything else;
  - creates their `users` rows (free tier, no Stripe links, reset on every run) and the admin's `AdminUser` row in the e2e database, and refuses a missing `DATABASE_URL` or one equal to the dev database;
  - never prints credentials.
- Re-running `npm run test:setup` reuses the stored credentials and resets the Firebase passwords to match.
- Accounts created before moved e2e to `yourapp-test` still exist in the dev project. deletes them.
- CI e2e uses only the dedicated Firebase test project `yourapp-test` (GitHub environment `e2e-test`) and Stripe test-mode keys. It must never use the development or production projects: the e2e job refuses live Stripe keys and refuses to run when `E2E_NEXT_PUBLIC_FIREBASE_PROJECT_ID` equals the repository variable `PROD_FIREBASE_PROJECT_ID`.

### Assertion rule

Update an assertion only when user-visible behaviour is unchanged, for example a selector that moved in a refactor. If the behaviour itself must change, raise it on the issue. Do not edit the assertion to match.

## Backend tests (pytest)

```bash
cd fastapi_backend
uv run pytest
```

- Config: `pytest.ini` sets `asyncio_mode = auto`.
- Tests live in `fastapi_backend/tests/`, mirroring `app/` (`api/`, `core/`, `services/`, `repositories/`, `commands/`). Most need **no database**: `tests/fakes.py` has a `FakeSession` and a `FakeSessionProvider` that implements the `SessionProvider` protocol, and tests inject it with `app.dependency_overrides[get_session_provider]` (clear overrides after the test). A repository test shows a repository working with an injected fake provider (ADR-004). The `*_e2e.py` files use a real Postgres through `TEST_DATABASE_URL` and are skipped without it; CI provides one through the `postgres` service in `backend.yml`. `tests/commands/test_seed_catalogue.py` also uses `TEST_DATABASE_URL` (it truncates the catalogue tables and refuses a database that is not a local `*_test` one).
- `tests/conftest.py` gives the app placeholder settings (`app/core/settings_environment.py`) because `app.core.config` reads them at import; see [Local `.env` and the test suite](#local-env-and-the-test-suite).

### Local `.env` and the test suite

The tests never see your machine's configuration. `app.core.config` builds its settings once, when it is first imported, from the process environment and a git-ignored `fastapi_backend/.env`; a developer's real `APP_NAME`, `SENTRY_DSN`, `EMAIL_PROVIDER` or keys must not change what the suite proves (a branded `APP_NAME` used to fail `test_openapi_identifies_yourapp` and the 12 `test_docs_are_not_served_in_production` cases, which end by asserting the schema title, and a real `SENTRY_DSN` made tests try to reach Sentry).

- **The switch.** `IGNORE_ENV_FILE=1` (read by `app/core/env_file.py`, truthy values `1`, `true`, `yes`, `on`) makes `Settings` and Alembic's `env.py` skip `.env`. Without it nothing changes: `uvicorn`, the management commands and Alembic still read `.env`. It is not a setting and is not in `.env.example`; do not set it for a normal run or in a deployment.
- **Who sets it.** `tests/conftest.py` calls `isolate_settings_environment(os.environ)` (`app/core/settings_environment.py`, which imports nothing that builds settings) before anything imports the app. It sets the switch, removes every name that is a field of `Settings` (compared case-insensitively and read from `config.py`, so a new setting is covered automatically) plus `DATABASE_URL_DIRECT`, and then pins the placeholders (`ENVIRONMENT=test`, the dummy keys, a dummy `DATABASE_URL`). pytest-xdist workers import the conftest too, and subprocesses the tests start (Alembic, the CLI e2e tests) inherit the clean environment. Settings must be read by field name only (no aliases, no `env_prefix`); `tests/test_env_isolation.py` fails otherwise.
- **What it covers.** Neither a `.env` file nor an exported variable such as `APP_NAME` or `ENVIRONMENT=production` reaches the settings during a run. `TEST_DATABASE_URL` is not a setting and is kept: it is how you point the `*_e2e.py` suites at a database.
- **The OpenAPI commands** (`commands.generate_openapi_schema`, `commands.check_openapi_schema`, the pre-commit hook, `watcher.py`) use the same helper (when the schema is generated, not when the module is imported) before the app loads, so they need no `.env` and no exported variable: they run in a bare shell and produce the same `nextjs-frontend/openapi.json` whatever `.env`, `APP_NAME`, `SENTRY_DSN` or `ENVIRONMENT` is present. The tests run them from a child environment built from scratch.
- **`scripts/gate.sh`** exports the switch for the whole run and pins `DATABASE_URL_DIRECT` to `GATE_TEST_DATABASE_URL` for the Alembic cycle (Alembic prefers `DATABASE_URL_DIRECT`, so an exported or `.env` value would otherwise point `downgrade base` at another database). The Alembic cycle drops every table, so before any step the gate refuses (a fixed-text `=== REFUSED:` line in the log, exit code 2; the URL itself is never printed) a `GATE_TEST_DATABASE_URL` whose host is not `localhost` or `127.0.0.1` or whose database name does not end in `_test`. It also refuses anything ambiguous: no `scheme://`, more than one `@`, a database name containing `@`, `/` or `#`, an IPv6 host or a second `:`, or a query string that overrides the connection (`host`, `hostaddr`, `database`, `dbname`, `port`, `user`, `password`). `GATE_GUARD_ONLY=1 GATE_TEST_DATABASE_URL=<url> bash scripts/gate.sh` runs just this check (no log, no step, no connection); `tests/test_gate_database_guard.py` drives it with a table of accepted and refused URLs. It skips the four Alembic steps when the backend pytest step failed (the result stays RED). Without `GATE_TEST_DATABASE_URL` it unsets `TEST_DATABASE_URL` and `DATABASE_URL_DIRECT`, so the run is really database-less.
- **Limit.** Settings you construct yourself in a test (`Settings()`, `Settings(_env_file=None)`) still read the process environment the test changes with `monkeypatch`, which is what those tests are for. Regression tests: `tests/core/test_env_file.py`, `tests/test_env_isolation.py` and the two cases in `tests/commands/test_generate_openapi_schema.py` and `test_check_openapi_schema.py`. Alembic's `env.py` reads exactly `fastapi_backend/.env` (no parent directory is searched), while `Settings` reads `.env` from the working directory, so the two are the same file only when you run from `fastapi_backend/`, as every documented command does. A validation error from `Settings` never prints the input values (`hide_input_in_errors`).

Lint and types, using the dev-group tools:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

- A bare `uv run mypy` checks `app` and `commands` (`mypy.ini` sets `files = app, commands`), the same scope as the pre-commit hook.
- mypy is blocking and runs in strict mode with zero errors; CI runs a plain `uv run mypy`.
- **Which `TEST_DATABASE_URL` is accepted.** The `*_e2e.py` suites rebuild the schema with Alembic, so they refuse (fail, not skip) any URL whose database name does not end in `_test` or whose host is not `localhost`, `127.0.0.1` or `::1`. A staging or shared server URL therefore never has its schema dropped. `tests/commands/test_seed_catalogue.py` also uses `TEST_DATABASE_URL` (it truncates the catalogue tables and refuses a database that is not a local `*_test` one).
- **The partner role test.** `tests/core/test_partner_role_e2e.py` and `tests/api/partner/test_partner_keys_e2e.py` apply `fastapi_backend/commands/sql/partner_role.sql` and connect as the role. Postgres roles are cluster-wide and the test cluster is shared, so they never create or drop a role called `freejoy_partner`: they use `freejoy_partner_<database name>` (`tests/partner_role_support.py`), created by the script if absent, never dropped, with grants applied only inside their own database. psql is not needed (the helper replaces the script's parameter block). The tests need a role that may create roles (the test cluster's superuser).
- **Parallel runs.** With a throwaway local Postgres (`TEST_DATABASE_URL` on a `*_test` database) the full suite is dominated by the real-route tests: each rebuilds the schema with Alembic and every test builds a fresh app, so a serial run took about 11 minutes. Run it in parallel: `uv run pytest -n auto --dist loadfile --cov` (CI does the same; about 2 minutes on a 20-core machine). `tests/conftest.py` gives each pytest-xdist worker its own database (`yourapp_test` becomes `yourapp_gw0_test`, `yourapp_gw1_test`, ..., created on first use, migrated to head, and rebuilt by the tests themselves), so workers never share a schema. Keep `--dist loadfile`: it runs a whole test file on one worker, so a file's schema is built once instead of on every worker. Each worker opens its own connections, so on a server with a low `max_connections` (default 100) cap the workers, for example `-n 8`; use Postgres 15 or newer. A plain `uv run pytest` (no `-n`) is unchanged and uses `TEST_DATABASE_URL` itself. Throwaway Postgres that is fast enough (data in memory, durability off): `docker run -d --name pgtest -p 127.0.0.1:55432:5432 -e POSTGRES_PASSWORD=<test password> --tmpfs /var/lib/postgresql/data postgres:17 -c fsync=off -c synchronous_commit=off -c full_page_writes=off -c max_connections=200`. Use `127.0.0.1`, not `localhost`, in `TEST_DATABASE_URL` (on Windows `localhost` can add a slow IPv6 lookup per connection), and add Windows Defender exclusions for the repo, its `.venv` and the uv cache. Serial runs on a default setup took about 30 minutes locally. The worker databases are left in place for reuse; drop them (`DROP DATABASE yourapp_gw0_test`, ...) when you remove your throwaway server.
- Coverage: `uv run pytest -n auto --dist loadfile --cov`. `fail_under` in `[tool.coverage.report]` of `pyproject.toml` is the backend ratchet, currently 90.0. It is set against the run without the Postgres-backed tests (91.5% measured), which is the lower figure; with them it measures 96.3%, and CI and `scripts/gate.sh` (with `GATE_TEST_DATABASE_URL`) run with them. Raise it when coverage rises, never lower it.
- Frontend lint: `npm run lint` runs `eslint .` with the flat config `eslint.config.mjs`.

## OpenAPI check

After changing a backend route, regenerate the schema:

```bash
cd fastapi_backend
uv run python -m commands.generate_openapi_schema
```

The command writes `nextjs-frontend/openapi.json` by default (`OPENAPI_OUTPUT_FILE` overrides it). It needs no `.env` and no exported variables, and ignores both ([Local `.env` and the test suite](#local-env-and-the-test-suite)). Then regenerate the typed client from `nextjs-frontend/`:

```bash
cd nextjs-frontend
npm run generate:client   # writes src/lib/api/generated (committed, excluded from eslint, prettier and coverage)
npm run check:client      # fails if the committed client differs from a fresh generation
```

Staleness is checked twice (ADR-008): the pre-commit `openapi-schema-check` hook (run by CI on changed backend files) compares the committed schema with the backend, and `openapi-client-check` plus the `OpenAPI client is up to date` step in `frontend.yml` compare the committed client with the schema. Server code calls the backend only through `src/lib/api/server.ts` (`callBackend`), which forwards the session cookie as `X-Session-Token` to `BACKEND_API_URL`.

## Continuous integration

`.github/workflows/ci.yml` runs on every pull request to `main` and every push to `main`:

| Job                               | Runs when                                                              | Checks                                                                                                   |
| --------------------------------- | ---------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| Detect changed paths              | always                                                                 | `dorny/paths-filter` decides frontend / backend                                                          |
| Pre-commit (changed files)        | pull requests                                                          | `pre-commit run --from-ref origin/<base> --to-ref HEAD`                                                  |
| Frontend (`frontend.yml`)         | `nextjs-frontend/**` or shared config                                  | lint, typecheck, Vitest with coverage ratchet, `npm run build` with placeholder `NEXT_PUBLIC_*` only     |
| Backend (`backend.yml`)           | `fastapi_backend/**` or shared config                                  | ruff lint and format, blocking mypy, pytest in parallel (`-n auto --dist loadfile`) with coverage ratchet |
| E2E (`e2e.yml`, its own workflow) | frontend or shared config changed and repo variable `E2E_ENABLED=true` | Chromium Playwright, secrets from environment `e2e-test`. Not in the gate |
| **CI gate**                       | always                                                                 | passes when every job above (except E2E) succeeded or was skipped                                        |

Shared config is `.github/**`, `.pre-commit-config.yaml`, root `package.json` / `package-lock.json`, `vercel.json` and the OpenAPI schema `nextjs-frontend/openapi.json`; it triggers both apps. A manual run (`workflow_dispatch`) runs both apps. `e2e.yml` is triggered on its own (`pull_request`, `push` to main, manual) with the same path filter, not called from `ci.yml`: a reusable workflow called from `ci.yml` received none of the `e2e-test` environment secrets (every `E2E_*` value was empty). The E2E job runs only for pull requests from this repository (not forks) and not for Dependabot. Its job declares `environment: e2e-test`, runs a guard step that fails naming any empty value (never printing it), then `npm run test:e2e:build` and `npm run test:e2e:baseline` against the e2e database. Actions are SHA-pinned and kept current by Dependabot (`.github/dependabot.yml`, weekly). Branch protection requires only **CI gate**.

## Before you open a PR

- Frontend: `npm run test:unit`, `npm run typecheck`, `npm run lint` and `npm run build`.
- Coverage: do not let it drop below the ratchet; CI enforces it.
- Backend: `uv run pytest -n auto --dist loadfile` (add `--cov` for the final check), `uv run ruff check .`, `uv run ruff format --check .`, and `uv run mypy` (zero errors).
- Chromium e2e when the change is user-facing.
