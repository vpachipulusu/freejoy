# Development workflow

## Branches, commits, pull requests

- Branch from `main`: `<type>/<short-slug>` (`feat/org-invites`, `fix/session-refresh`).
- Conventional commits: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`, `ci:`, `perf:`.
- Open a pull request with the template in `.github/PULL_REQUEST_TEMPLATE.md`: what changed, how you verified it
  (commands and results, including any Playwright specs you ran locally), what reviewers should look at.
- Protect `main` so that only the `CI gate` check is required and merges go through pull requests.

## Before you push

```bash
# frontend (nextjs-frontend/)
npm run lint && npm run typecheck && npm run test:unit && npm run build
# backend (fastapi_backend/)
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest --cov
# both, on changed files only
uvx pre-commit run --from-ref origin/main --to-ref HEAD
```

Install the git hook once with `uvx pre-commit install` if you want these to run on every commit.

## CI

`.github/workflows/ci.yml` detects which paths changed and runs only the matching workflows:

- `frontend.yml`: lint, typecheck, unit tests with coverage, a server-only-package grep, production build with
  placeholder Firebase env.
- `backend.yml`: ruff, mypy, pytest (including the Postgres-backed `*_e2e.py` tests against a service
  container), coverage floor, and an Alembic job that runs `upgrade head`, `alembic check` and `downgrade base`.
- Pre-commit on the files changed by the PR, and a Docker Compose smoke test.
- `CI gate` always runs last and passes when every needed job succeeded or was skipped by the path filter.

`e2e.yml` (Playwright) is separate and off until you create a Firebase test project and set the `e2e-test`
environment secrets; see [local-e2e.md](local-e2e.md) for running specs locally in the meantime.

## The generated API client

The frontend calls the backend through a typed client generated from `nextjs-frontend/openapi.json`. After
changing a route, schema or error response in the backend:

```bash
cd fastapi_backend && uv run python -m commands.generate_openapi_schema
cd ../nextjs-frontend && npm run generate:client && npm run check:client
```

Commit `openapi.json` and `src/lib/api/generated/` together with the backend change. Never edit the generated
files by hand; pre-commit and CI fail when they are stale.

## Database migrations

One Alembic head at a time. After changing a model:

```bash
cd fastapi_backend
uv run alembic revision --autogenerate -m "<what changed>"
# add the new revision id to PLATFORM_REVISIONS in app/core/platform_schema.py
uv run alembic upgrade head && uv run alembic check && uv run alembic downgrade base && uv run alembic upgrade head
```

Run migrations yourself before or right after deploying (no deploy does it for you); `GET /health/ready`
answers 503 `migrations_pending` until then. Details: [platform-models.md](platform-models.md).

## Dependencies

- Frontend: `npm install <pkg>` from `nextjs-frontend/`; commit `package-lock.json`.
- Backend: edit `pyproject.toml`, then `uv lock` and
  `uv export --frozen --no-dev --no-hashes --quiet -o requirements.txt` (pre-commit checks the export).
- Dependabot opens weekly grouped updates for npm, pip and GitHub Actions (`.github/dependabot.yml`).

## Owner setup: scheduled housekeeping

`.github/workflows/scheduled-housekeeping.yml` runs three backend commands every day at 03:17 UTC
(`check_offer_health`, `prune_click_events`, `purge_subscribers`; details in
[backend-offer-health.md](backend-offer-health.md#scheduling)). It is outside the `CI gate`.

**Create a GitHub Environment named `housekeeping`** (Settings, Environments, New environment) and keep the
credentials there, not in repository secrets. A repository secret can be read by any workflow on any branch, and
`workflow_dispatch` lets a modified copy of the workflow on a feature branch receive it. An environment secret is
released only to a job that targets the environment, and the environment's deployment-branch policy decides which
branches may do that:

- **Deployment branches and tags:** "Selected branches and tags", with one rule, `main`.
- **Environment secrets:**
  - `HOUSEKEEPING_DATABASE_URL` (required): the production database URL for a **dedicated role** with only the
    rights these commands use (do not reuse the application's owner role, and never the migration role). End it with
    `?sslmode=verify-full`. `sslmode=require` encrypts but checks neither the certificate nor the host name, and this
    connection crosses the public internet from a GitHub-hosted runner, so an on-path party could terminate TLS and
    read the credential; `verify-full` closes that (`app/core/db_url.py` maps `verify-full` to a default SSL context that trusts the system CA store and checks
    the host name unless `PGSSLROOTCERT` names a root certificate file; asyncpg's own string mode would trust only that file and fail without it). From the repositories' SQL, the role
    needs:

    | Table | Rights | Used by |
    | --- | --- | --- |
    | `offers` | `SELECT`, `UPDATE` (`status`, `updated_at`) | `check_offer_health` reads offers to expire and to check, locks them (`FOR UPDATE SKIP LOCKED`) and sets `status` |
    | `affiliate_links`, `merchants` | `SELECT` | `check_offer_health` reads the `direct` links to check and each merchant's website |
    | `audit_logs` | `INSERT`, `SELECT` | `check_offer_health` writes one `admin.offers.expire` row per expired offer (SQLAlchemy reads the generated `created_at` back with `RETURNING`, which needs `SELECT`) |
    | `click_events` | `SELECT`, `DELETE` | `prune_click_events` |
    | `email_subscribers` | `SELECT`, `DELETE` | `purge_subscribers` |

    No other table, no DDL and no `alembic_version`. This list comes from reading the SQL, not from running the
    commands as a restricted role: after creating the role, run the workflow by hand once and read the step logs; a
    "permission denied" failure names the missing right, so widen by exactly that one.
  - `SENTRY_DSN` (optional): the same DSN as the deployed backend. Without it nothing is sent to Sentry; the commands
    and their logs work as before.
- **Environment variable** (not a secret): `PUBLIC_SITE_URL` (required), the website's https origin, for example
  `https://example.com`. `check_offer_health` puts it in the User-Agent it sends to merchants
  (`<Brand>-LinkCheck/1.0 (+<PUBLIC_SITE_URL>)`); unset, merchants would see `http://localhost:3000`. Use the same value
  as the deployed backend.

The first step of every run is a guard: it fails with a clear message when `HOUSEKEEPING_DATABASE_URL` is empty or
`PUBLIC_SITE_URL` is empty or is not an `https://` origin, before anything is installed. **A manual dispatch from any
branch other than `main` fails by design**: the environment's branch rule refuses the job (and a copy of the workflow
that somehow started would find no secret and stop at the guard). To test a change to the workflow, merge it first, or
temporarily add that branch to the environment's rule and remove it afterwards.

The database must accept TLS connections from GitHub-hosted runners, whose addresses are not fixed: a hosted
Postgres with an IP allow-list needs to allow them (or use a provider that does not filter by address). Check with
"Run workflow" on `main` (tick `skip_reachability` for a first run that makes no outbound request). Nothing is
introduced in the apps' environment; the workflow's placeholder settings are explained in its comments.

## Keeping the template's structure

- Module code stays behind the touch points in [modules.md](modules.md) so the module remains removable.
- Record a design decision as an ADR (`docs/adr/`, template in its README) when it changes how the apps talk
  to each other, where data lives, or how requests are authenticated.
