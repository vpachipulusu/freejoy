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
- **Parallel runs.** With a throwaway local Postgres (`TEST_DATABASE_URL` on a `*_test` database) the full suite is dominated by the real-route tests: each rebuilds the schema with Alembic and every test builds a fresh app, so a serial run took about 11 minutes. Run it in parallel: `uv run pytest -n auto --dist loadfile --cov` (CI does the same; about 5 to 6 minutes with coverage on a 20-logical-core machine, see What coverage costs the backend stage (FREEJ-65) below). `tests/conftest.py` gives each pytest-xdist worker its own database (`yourapp_test` becomes `yourapp_gw0_test`, `yourapp_gw1_test`, ..., created on first use, migrated to head, and rebuilt by the tests themselves), so workers never share a schema. Keep `--dist loadfile`: it runs a whole test file on one worker, so a file's schema is built once instead of on every worker. Each worker opens its own connections, so on a server with a low `max_connections` (default 100) cap the workers, for example `-n 8`; use Postgres 15 or newer. A plain `uv run pytest` (no `-n`) uses `TEST_DATABASE_URL` itself. Never run two pytest sessions against the same base URL at once (see Load-sensitive tests and stalls below). Throwaway Postgres that is fast enough (data in memory, durability off): `docker run -d --name pgtest -p 127.0.0.1:55432:5432 -e POSTGRES_PASSWORD=<test password> --tmpfs /var/lib/postgresql/data postgres:17 -c fsync=off -c synchronous_commit=off -c full_page_writes=off -c max_connections=200`. Use `127.0.0.1`, not `localhost`, in `TEST_DATABASE_URL` (on Windows `localhost` can add a slow IPv6 lookup per connection), and add Windows Defender exclusions for the repo, its `.venv` and the uv cache. Serial runs on a default setup took about 30 minutes locally. The worker databases are left in place for reuse; drop them (`DROP DATABASE yourapp_gw0_test`, ...) when you remove your throwaway server.
- Coverage: `uv run pytest -n auto --dist loadfile --cov`. `fail_under` in `[tool.coverage.report]` of `pyproject.toml` is the backend ratchet, currently 91.0. It is set against the run without the Postgres-backed tests (92.3% measured), which is the lower figure; with them it measures 98.6%, and CI and `scripts/gate.sh` (with `GATE_TEST_DATABASE_URL`) run with them. Raise it when coverage rises, never lower it. The tracer is `core = "sysmon"` in `[tool.coverage.run]`; see **What coverage costs the backend stage (FREEJ-65)** below before changing it.
- Frontend lint: `npm run lint` runs `eslint .` with the flat config `eslint.config.mjs`.

## Load-sensitive tests and stalls (FREEJ-64)

The suites run on a machine that is usually busy: other developers' suites, the gate, antivirus. Two kinds of failure showed up only then, and both are about how a test waits, not about what it checks.

**What made tests load-sensitive.**

- **A pause per keystroke.** `userEvent.setup()` waits a zero-delay timer after every key and click. On Windows that is up to a scheduler tick (about 25 ms measured), so typing a sign-in form cost about 0.4 s and the two-factor file took 15 s alone (`findBy*` gives up after 1 s and a test after 5 s, so a busy machine turned a slow test into a failed one). Use `setupUser()` from `src/__tests__/unit/helpers/user.ts` (no pause; the same keydown, input and keyup events fire); `TestUser` is its type for helpers. An ESLint rule on `src/__tests__/**` forbids calling `userEvent.*` directly. `totp-challenge.test.tsx`: 15.2 s before, 3.4 s after, on the same machine.
- **A result on screen before the control is idle.** A transition can show its result while `pending` is still true, so a `getByRole` for the idle button taken right after `findByText(result)` finds "Checking..." or a disabled button. Wait for the idle control with `findIdleControl()` (`helpers/idle.ts`: found again on every retry, enabled, not `aria-disabled="true"`, not inside an `aria-busy` region) before clicking it again, or `waitFor` the state you assert.
- **A bare wall-clock bound.** `elapsed < 50` measured the machine (a 20 ms scrub read 50+ ms when two suites ran together).
- **Too many vitest forks starting at once.** Vitest starts one fork per test file, up to cores minus one at a time, and each must report started within 60 s; on a stalled machine 12 of them did not ("Failed to start forks worker ... Timeout waiting for worker to respond"). The gate caps the pool (`GATE_VITEST_MAX_WORKERS`, default 8, passed as `VITEST_MAX_WORKERS`).
- **Workers sharing a database**, or a session left open (below).

**Rules.**

1. **No wall-clock assertions.** Prove the property instead. Linear-time checks compare the cost at `n` with the cost at `n / 4` and bound the ratio (frontend `helpers/linear-growth.ts`: every sample kept, the check passes when the ratio of the cheapest calls OR of the median calls is under the bound, its retry budget is 2.5 s so it reports its own ratio before vitest's 5 s test timeout; backend `tests/cpu_scaling.py`, on CPU time). Measure below any cap the code applies (`redactString` keeps 10,000 characters, so a longer input proves nothing). Cycle and shared-child checks count how often each node is walked (`walked()` in `sentry-scrub-hardening.test.ts`), which cannot depend on speed. A timeout that only guards against a hang (`asyncio.wait_for(..., 15)`) is allowed: it never decides a result, it only ends a stuck test.
2. **Never raise a timeout globally** to make a test pass. Explain the cost and fix it; a timeout on one test whose cost is understood is the last resort.
3. **A fixture never holds a session or transaction across a schema rebuild.** `alembic downgrade base` needs locks, and a session holding a conflicting lock (typically one left inside a transaction) blocks it. Close the session first.
4. **Every subprocess has a timeout**, and every fixture's schema rebuild goes through `run_alembic()` in `tests/alembic_support.py` (300 s limit, a clear `AlembicStalled` or `AlembicFailed` instead of a bare `CalledProcessError`). Other children use `CHILD_TIMEOUT_SECONDS` (`tests/_subprocess_support.py`). Connections a fixture opens carry a connect timeout, and the checks `run_alembic` itself makes carry client-side time limits too.

**What is and is not explained about the stall of 2026-11-11.** Two xdist workers stood still for more than 30 minutes; their sessions in Postgres were `idle in transaction` with a wait on `ClientRead` (waiting for their own client), opened within 15 ms of each other near the start of the run, and ending those sessions on the server did not release the workers. That is NOT a lock wait: no session was `active` and waiting on a lock, and `show standard_conforming_strings` is the last statement of SQLAlchemy's dialect initialisation on an engine's first connection, so the clients stopped between two awaits. An earlier version of this section, the docstrings and the commit message of e326d02 blamed a lock held while Alembic ran; that was a hypothesis, it was not shown, and it does not fit this evidence. It was correct only for a different, real hazard (a leaked session blocking `DROP TABLE`), which `run_alembic` now also handles. **The cause of the original stall is not known.** What the branch adds is that a stall of this kind is now bounded and shows where it stands: the hard per-test limit below ends the stuck worker and prints every thread's Python stack.

**What the backend does about stalls.**

- **A hard per-test limit.** `tests/conftest.py` arms `faulthandler.dump_traceback_later(900, exit=True)` around every test (fixtures included; 900 s is above two Alembic limits). A test still running then has the stack of every thread written to stderr and the process ends at once. This replaces `faulthandler_timeout` (the two share one timer; `pytest.ini` sets neither). Observed with a deliberate 3 s limit on a test that sleeps for 60 s (`FREEJOY_HARD_TEST_LIMIT_SECONDS` sets the limit, for the tests of the hook only): in a plain run the dump reaches the console, the process ends, the exit code is 1 and the tests after it are not run; under xdist the dump reaches the console from the worker, the controller reports `worker 'gwN' crashed while running '<test>'`, xdist starts a replacement worker which runs the hung test again, and with xdist's default of 4 x workers restarts that was 9 crashes (nine times the limit). `pytest.ini` therefore sets `--max-worker-restart=1`: one replacement (so at most two limits), then the run stops with a non-zero exit code. The stack alone names the test and the line.
- A stalled Alembic child is started with its own `faulthandler` timer 20 s before its limit (90% of it for a short limit): it prints its Python stack and exits, and `AlembicStalled` carries the stack and the list of sessions on the database (state, wait event, `pg_blocking_pids`, locks held and waited for, transaction age, last query). Otherwise the sessions are listed first and the child is then killed. A child that fails on the database's `lock_timeout` raises `AlembicFailed` with the end of its output and the same session list.
- `run_alembic` first ends client sessions another test left on the process's own database (`pg_terminate_backend`; only when this process holds the database guard, only local `*_test` databases, only sessions that are busy or have been idle for more than 2 s, never the guard), and warns with a `LeakedSessionWarning` that names each one (application name, transaction start, last query). A module-scoped fixture in `conftest.py` also fails the module that leaves a session inside a transaction on the run's database. `scripts/gate.sh` prints `=== backend pytest: <n> leaked sessions were ended` after the pytest stage, green or not.
- `tests/db_guard.py` (called from `tests/conftest.py` at import, so an exported `TEST_DATABASE_URL` also refuses a second run that selects only tests needing no database) takes a session-level advisory lock on the database a pytest process uses and sets `lock_timeout` (60 s), `idle_in_transaction_session_timeout` (180 s) and `statement_timeout` (240 s) on it with `ALTER DATABASE`. A lock wait therefore ends with an error naming the statement. **These settings persist** on `<name>_test` and on every `<name>_gwN_test` the run touched, apply to psql and every other session on them (including the gate's own Alembic cycle), and are undone with `ALTER DATABASE <db> RESET lock_timeout;` (and `RESET idle_in_transaction_session_timeout;`, `RESET statement_timeout;`) for each database. A debugger pause of more than 3 minutes inside a transaction is ended by the server. A test that needs a shorter limit sets its own with `SET LOCAL`. The guard fails closed: in a worker, or on any database error, the run stops with a message; only an unreachable server in a plain run is left to the tests that need it.
- **Never run two pytest sessions against the same base `TEST_DATABASE_URL`** (two terminals in one checkout, or a manual run during the gate). Both name their workers `gw0`, `gw1`, ... so both use `<name>_gwN_test` and one rebuilds the schema under the other; this shows up as `ProgrammingError` in unrelated e2e tests. The second run now stops at start with "another pytest process is already using the test database", naming the holder's pid and start time. Give each concurrent run its own database name (`yourapp_a_test`, `yourapp_b_test`).

**Reading a `faulthandler` dump.** It starts with `Timeout (0:15:00)!` (a child's dump: `Timeout (0:04:40)!`), then one block per thread, most recent call first. Find the block whose frames include your test file (the worker's main thread) and read its first lines: `subprocess.py ... communicate` or `wait` means it is waiting for a child (look for an Alembic or CLI child and its `AlembicStalled` message); `asyncio/base_events.py ... run_forever` or `select` means it awaits something that never completes (a lock, an event, a task); `asyncpg` frames mean it awaits the server. Threads named in `db_guard.py` (`_hold`) are the guard and are always there. Under xdist the dump appears on the console without naming the worker; the test file in the stack tells you which one. To see which sessions block which, connect to the `postgres` database and run: `SELECT datname, pid, application_name, state, wait_event_type, wait_event, pg_blocking_pids(pid) AS blocked_by, now() - xact_start AS in_transaction, left(query, 80) FROM pg_stat_activity WHERE datname LIKE '%\_test' AND backend_type = 'client backend';`.

**The gate's time limits.** Every stage of `scripts/gate.sh` has a limit: `GATE_STAGE_TIMEOUT_SECONDS` (default 900), except the frontend unit stage (`GATE_VITEST_TIMEOUT_SECONDS`, default 1800) and the backend pytest stage (`GATE_PYTEST_TIMEOUT_SECONDS`, default 2700). A non-numeric value stops the gate with exit code 2. The log starts with the mechanism in use. On Linux and macOS it is coreutils `timeout`, which signals the stage's process group. On Git Bash it is a watchdog that polls the stage and at the limit runs `taskkill /F /T` on its Windows pid (from `/proc/<pid>/winpid`), because `timeout` there only signals MSYS processes and was suspected of leaving `uv`'s children, xdist workers and vitest forks running with their database sessions and advisory locks (so the next gate would refuse to start). What was proven: the watchdog ends a native Python grandchild started by the stage (`tests/test_gate_timeout.py`, a marker file that never appears). What was not: with plain coreutils `timeout` on the same Git Bash (8.32) a plain, a detached and a base-interpreter grandchild were also ended, so the survivors could not be reproduced, and the test does not distinguish the two mechanisms. With neither mechanism the stage runs without a limit and the log says so. A stage over its limit is ended and the gate prints `=== <stage> TIMED OUT after N minutes and was ended with its process tree ...` and ends RED (the decision comes from the mechanism's own result and the time used, not from an exit code of 124 or 137 that a command could return itself). Ctrl-C and `kill` end the running stage's tree and exit 130. Every stage line carries the time (`=== name [14:02:11]`) and its exit line the seconds it took (`elapsed=312s`). Any failing stage prints the last `GATE_TAIL_LINES` (default 60) lines of its output to the console as well as writing them to the log. `GATE_SELF_TEST_COMMAND='sleep 120' GATE_PYTEST_TIMEOUT_SECONDS=2 bash scripts/gate.sh <log>` runs only the stage machinery on that command: it prints `=== SELF TEST ONLY: no check runs; this is not a gate result`, ends with `=== GATE SELF TEST: stage passed|failed; NOT A GATE VERDICT`, never prints a gate result line, and exits 3 when the stage passed and 1 when it failed.

**Stale worker databases.** The per-worker databases (`<name>_gwN_test`) are created once and reused. If you edit an unreleased migration in place (or switch to a branch with another version of one), a worker database migrated with the other version can no longer be downgraded: `alembic downgrade base` then failed in every worker (`run_alembic` now shows the error and its output). Drop the worker databases whenever you change a migration in place, with no pytest run active: `DROP DATABASE <name>_gw0_test` for each worker number (list them with `SELECT datname FROM pg_database WHERE datname LIKE '%\_gw%\_test'`). They are recreated and migrated on the next run.

**What coverage costs the backend stage (FREEJ-65).** The gate's stage `backend pytest+cov (with e2e)` took 878 s on `f14dac0` (FREEJ-64 merged), against 344 s on `0c35c1d` just before it, and FREEJ-64's protections were the first suspects. They are not the cause. Measured on one machine (20 logical cores, a local Postgres, nothing else started by the person measuring; a fixed CPU probe read the same before every run), `uv run pytest -n auto --dist loadfile` with the database, wall seconds, one to three runs per cell:

| Code | no coverage | `--cov`, default tracer (CTracer) | `--cov`, `core = "sysmon"` |
| --- | --- | --- | --- |
| `0c35c1d` (before FREEJ-64), from a scratch copy | 273, 270 | 745, 639 | not run |
| `f14dac0` (FREEJ-64 merged), in the worktree | 276, 283 | 715, 642 (and 695 from a scratch copy) | 312 |
| FREEJ-65 (`f14dac0` plus the coverage setting and 4 tests) | not run | not run | 345, 351, 311 |

- **The cost is the tracer, and it is the same before and after FREEJ-64.** With coverage on, the same code ran 2.3 to 2.7 times slower on both commits (one test that builds the app: 2.0 s without coverage, 6.9 s with CTracer, 1.9 s with sysmon). The gate logs on `main` (all CTracer, `COVERAGE_CORE` unset, total 96.5% to 96.6%, same machine, pytest's own time) read 468.64 s (5,529 tests), 312.16 s (5,683 tests), 505.48 s (5,719 tests), 343.96 s (6,416 tests, `0c35c1d`) and 878.61 s (6,442 tests, `f14dac0`): the CTracer run varied between 312 s and 878 s from one gate to the next, and why it varies that much is not established. The runs above are measurements of one session; they did not reproduce the 344 s of `0c35c1d` (639 s and 745 s). (The scratch copies lack `nextjs-frontend/`, so 44 to 51 tests that read it failed or were skipped there; that is under 2% of the run.)
- **Each FREEJ-64 mechanism, measured.** The hard per-test limit (`faulthandler.dump_traceback_later` armed and cancelled around a test): 0.07 ms per test without coverage, 0.2 ms with sysmon, about 1.5 s for all 6,446 tests added up over every worker. The module leak check and the check before each Alembic child (a thread, an event loop and a new connection per call): 21 to 25 ms and 21 to 38 ms per call, 231 test modules, so under 10 s added up over every worker (under 1 s of wall time). The database guard (a daemon thread with an idle event loop) is claimed once per process: 42 to 59 ms. The Alembic child started through `python -c` is not measured by coverage at all (no `patch = subprocess` and no `COVERAGE_PROCESS_START`), and its schema rebuild took the same 4.8 to 5.0 s in a module's first test before and after FREEJ-64. Last, a 591-test slice (the `*_e2e.py` modules under `tests/api`, `-n 8 --cov`) with all four mechanisms switched off by a temporary local edit took 300 s and 346 s; with all of them on, 310 s and 303 s. The difference is noise. Nothing was restructured and no protection was removed.
- **What changed.** `[tool.coverage.run] core = "sysmon"` (Python 3.12's `sys.monitoring`, line coverage only, which is all this project measures). It also fixes a measurement error: CTracer without `concurrency = greenlet` loses its place when SQLAlchemy's asyncio layer switches greenlets, so application lines that run after an awaited database call go unrecorded (the `*_e2e.py` tests exercise exactly that code). On `tests/api/admin_subscriptions/test_cancel.py` alone, `app/services/admin_subscriptions.py` read 36% with CTracer, 46% with sysmon, and exactly the same 46% (same missing lines) with CTracer and `concurrency = ["greenlet", "thread"]`, which is slower than both. So total backend coverage moved from 96.54% to 98.63% with the database and from 91.5% to 92.29% without it: the code did not change, the measurement became correct. CI already ran with sysmon: `backend.yml` set `COVERAGE_CORE: sysmon` since the first commit, so only local runs and `scripts/gate.sh` changed tracer, and the move from 96.54% to 98.63% describes those runs. FREEJ-65 removed that variable from `backend.yml` so that `pyproject.toml` is the only place the core is chosen. CI was not run for this story. `fail_under` went from 90.0 to 91.0 (about 1 point under the database-less figure, as before). `tests/test_coverage_config.py` keeps the setting from being dropped: one test reads `pyproject.toml` (and rejects `concurrency`, `branch`, `dynamic_context` and `timid`, each of which makes coverage fall back to CTracer), another asks `sys.monitoring` which tool holds the coverage slot when the run is measuring (a `COVERAGE_CORE` variable in the environment overrides the setting, so only this test catches it).
- **Expected stage times** on the same machine: with the database about 307 to 351 s (before: 640 to 745 s with coverage; 270 to 283 s without), without the database about 180 to 260 s (two runs: 259 s and 181 s). They move with the machine: a run of the same code took 639 s and 745 s on two occasions.
- **If you change the tracer back,** expect the full run to take about two and a half times as long and the report to lose application lines that run after an awaited database call. A profile with CTracer shows the cost in the tests themselves (building the app, generating OpenAPI), not in setup or teardown.
- **The vitest split was measured and not made.** Running the 118 plain `.ts` test files in `node` instead of `jsdom` (3 of them need `jsdom`) took 9.6 s against 13.6 s, so about 4 s of a 57 s unit run (7%, below the 15% that would justify a second project, a per-file environment list and a second setup file).

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
