# CLAUDE.md (fastapi_backend)

Guidance for agents working on the FastAPI service. Repo-wide rules are in
[`../.claude/CLAUDE.md`](../.claude/CLAUDE.md). Run every command below from `fastapi_backend/`. Decisions:
[ADR-003](../docs/adr/0003-nextjs-to-fastapi-authentication.md),
[ADR-004](../docs/adr/0004-backend-layering.md), [ADR-007](../docs/adr/0007-rate-limit-keys.md),
[ADR-008](../docs/adr/0008-api-client-generation.md).

## Commands

```bash
uv sync                                          # install (Python 3.12, uv only; no pip)
uv run fastapi dev app/main.py --host 127.0.0.1 --port 8000 --reload   # or --port $BACKEND_PORT
uv run ruff check .                              # lint (add --fix to apply)
uv run ruff format .                             # format (CI runs --check)
uv run mypy                                      # strict; checks app/ and commands/
uv run pytest                                    # all tests, no network, no database
uv run pytest -n auto --dist loadfile --cov --cov-report=term-missing:skip-covered   # CI form (parallel)
uv run alembic upgrade head                      # only against a database you own (see Alembic)
```

- **Coverage floor.** `[tool.coverage.report] fail_under` in `pyproject.toml` fails `pytest --cov` below the
  floor (89.0: about 1 point under the 89.9% measured without the database; 95.6% with it). It is a ratchet:
  raise it when coverage rises, never lower it.
- **OpenAPI and the client.** After any change to routes, schemas or `ERROR_RESPONSES`, regenerate the schema and
  the TypeScript client:

  ```bash
  uv run python -m commands.generate_openapi_schema   # writes ../nextjs-frontend/openapi.json (LF)
  cd ../nextjs-frontend && npm run generate:client    # then: npm run check:client
  ```

  Commit `openapi.json` and `src/lib/api/generated/`. `uv run python -m commands.check_openapi_schema` fails when
  the committed schema is stale.

- **Dependencies.** Edit `pyproject.toml` (pin exact versions for risky or fast-moving packages), then
  `uv lock` and `uv export --frozen --no-dev --no-hashes --quiet -o requirements.txt`. The pre-commit hook fails
  when `requirements.txt` is out of sync.
- **Alembic and models.** Platform models are in `app/models/platform/` (one aggregate per file, ids from
  `app.core.ids.new_id`, snake_case, timezone-aware timestamps); register new ones in its `__init__.py`.
  `alembic_migrations/` has the baseline `0001`; `env.py` uses `DATABASE_URL_DIRECT` else `DATABASE_URL`. No
  deploy runs `upgrade head` (ADR-011): run migrations yourself. Create a revision with
  `uv run alembic revision --autogenerate -m "<msg>"`, add its id to `PLATFORM_REVISIONS` in
  `app/core/platform_schema.py`, then run `upgrade head`, `alembic check` and `downgrade base` against a database
  you own (CI does it on a Postgres 17 container). See
  [`../docs/developer/platform-models.md`](../docs/developer/platform-models.md).
- **Pre-commit.** `uvx pre-commit run --from-ref origin/main --to-ref HEAD` before opening a PR.
- Keep LF line endings in committed files.

## Layering (routers > services > repositories)

```
app/api/v1/        routers: HTTP only. Parse and validate input, call one service, shape the response.
app/api/           dependencies (deps.py), rate-limit dependencies (rate_limit.py), route ids.
app/services/      business logic and transaction boundaries. No FastAPI imports.
app/repositories/ SQLAlchemy queries for one aggregate each. No business rules. Receive an AsyncSession.
app/core/          config, logging, errors, request id, rate-limit primitives, database seam, Sentry.
```

Import direction is one way: routers may import services, schemas and `core`; services may import repositories
and `core`; repositories may import `core` and SQLAlchemy models only. Never import upward (a service must not
import `fastapi`, a repository must not import a service). Repositories never create sessions, engines or
session makers: those live behind `app.core.database` (`SessionProvider`), and routers get them through
`app/api/deps.py`, the single place where concrete implementations are wired.

## Conventions

- **Error envelope.** Every error body is `{"success": false, "error": "<message>"}` (`app.core.errors`).
  Raise `HTTPException` (or a domain error mapped to one) with a plain message; validation errors become a 422
  with `"Invalid request"`; anything unhandled becomes a 500 with `"Internal server error"` and no internals.
  New routes need no extra `responses=` for 422 and 500: they are declared on the app.
- **Request ID.** `RequestIdMiddleware` generates an ID per request (never trusted from the client), returns it
  as `X-Request-ID`, stores it in `request_id_var`, and tags Sentry with it. Log with the standard `logging`
  module (JSON, no `print`); the ID is added to every record automatically.
- **Sentry.** `app.core.sentry.init_sentry` runs in `create_app()` and does nothing unless `SENTRY_DSN` is set
  (leave it unset locally and in tests). Environment comes from `VERCEL_ENV`, else `ENVIRONMENT`; release from
  `VERCEL_GIT_COMMIT_SHA`. Events are scrubbed in `scrub_event`: header names, contexts and extra keys matching
  the `SENSITIVE_NAME` denylist (auth, cookie, token, key, secret, session, password) are filtered; bodies,
  cookies and query strings are dropped; log breadcrumbs are dropped; frame locals and tracing are off. The 500
  handler only logs: the SDK reports the exception once. To report something you handle yourself, call
  `app.core.sentry.report_exception(exc)`.
- **Rate limiting (ADR-007).** Per-route dependencies in `app/api/rate_limit.py`: `limit_by_subject` for
  authenticated routes, `limit_auth_attempts` for sign-in, sign-up and password reset. Policies live in
  `app/core/rate_limit.py`; the limiter is injected via `get_rate_limiter`. Auth policies fail closed (503),
  api and webhook policies fail open with a warning and a Sentry event.
- **Config and fail-fast.** All settings come from `app.core.config.Settings` (pydantic-settings); never read
  `os.environ` elsewhere. Required values (`DATABASE_URL`) and production-only requirements (Upstash, Firebase
  Admin, the internal key) are validated at import time so a misconfigured deployment refuses to start.
  `ENVIRONMENT` defaults to `production`; only `local` and `test` (or Vercel `preview`/`development`) relax
  checks. Secrets are `SecretStr` and are never logged. Add new variables to `.env.example` and
  `docs/developer/getting-started.md`. The only `os.environ` reader besides `Settings` is the `IGNORE_ENV_FILE`
  switch in `app/core/env_file.py`, which stops `.env` being read (tests, the OpenAPI commands and the gate set it;
  see `docs/developer/testing.md`). Never set it in a deployment or for a normal local run.
  Fields are read by plain name (no alias, no `env_prefix`): the test and schema isolation removes them by name.
- **Modules.** The Stripe Billing code is listed in `docs/developer/modules.md`; keep new billing code inside
  the files listed there so the module stays removable.
- **Style.** Strict typing (mypy strict), small functions, immutable data (`@dataclass(frozen=True)`), no
  hardcoded secrets, docstrings only where the why is not obvious.

## Test conventions

- Tests live in `tests/` mirroring `app/` (`api`, `core`, `services`, `repositories`, `commands`). Unit tests use
  no network, real database, Redis or Sentry: `tests/conftest.py` sets placeholder settings. Tests named
  `*_e2e.py` run against a throwaway Postgres when `TEST_DATABASE_URL` points at a `*_test` database and are
  skipped otherwise. `tests/commands/test_seed_catalogue.py` also uses `TEST_DATABASE_URL`: it truncates the
  catalogue tables and refuses a database that is not a local `*_test` one.
- **Fakes via Protocol, not mocks of internals.** Depend on a small `Protocol` (`SessionProvider`,
  `RateLimiter`) and inject a fake from `tests/fakes.py` (`FakeSessionProvider`, `FakeRateLimiter`,
  `RaisingRateLimiter`). In API tests replace the dependency with `app.dependency_overrides`, or build a small
  FastAPI app with only the pieces under test, and clear overrides afterwards.
- **AAA.** Arrange, Act, Assert, in that order, one behaviour per test, named for the behaviour
  (`test_returns_503_when_database_is_unreachable`).
- API tests use `httpx.AsyncClient` with `ASGITransport` (`raise_app_exceptions=False` to see the 500
  envelope); `asyncio_mode = auto`, so async tests need no marker.
- For Sentry, use a recording `Transport` (see `tests/core/test_sentry.py`); never send real events.
- Fix the implementation, not the test, unless the test is wrong. New code ships with tests; keep coverage at or
  above the floor.
