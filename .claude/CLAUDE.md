# CLAUDE.md

Guidance for coding agents working in this repository. Backend-specific rules: `fastapi_backend/CLAUDE.md`.

## Repository layout

Monorepo with two apps and one direction of calls (the browser never calls FastAPI):

- `nextjs-frontend/` — Next.js 16 app (App Router, React 19, Tailwind, shadcn/ui). **Run every npm command from this folder. npm only; no pnpm or yarn.**
- `fastapi_backend/` — FastAPI service (Python 3.12, uv, SQLAlchemy async, Alembic). Adapted from vintasoftware/nextjs-fastapi-template.
- `docs/developer/` — developer docs (start at `README.md`); `docs/adr/` — architecture decision records.
- `.github/workflows/` — path-filtered CI ending in the single required check `CI gate`.

## Commands

```bash
# Frontend (nextjs-frontend/)
npm run dev              # dev server (webpack; predev hook cleans stale locks)
npm run build            # production build
npm run lint             # ESLint
npm run typecheck        # tsc --noEmit
npm run test:unit        # Vitest (src/__tests__/unit)
npm run test:coverage    # Vitest with coverage (ratchet thresholds in vitest.config.ts)
npm run test:e2e:local -- <spec>   # Playwright against a production build; needs a running backend
npm run generate:client  # openapi.json -> src/lib/api/generated (backend changes only)
npm run check:client     # fails when the committed client is stale

# Backend (fastapi_backend/)
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest --cov                                     # coverage ratchet (fail_under in pyproject.toml)
uv run alembic upgrade head && uv run alembic check     # models must match migrations
uv run python -m commands.generate_openapi_schema       # writes ../nextjs-frontend/openapi.json
```

## Next.js 16 proxy (not middleware)

Route protection, rate limiting, CORS and security headers live in `src/proxy.ts`, exported as
`export async function proxy(request: NextRequest)`. Naming it `middleware` fails the build. Admin role checks
cannot run there (Firebase verification needs Node.js); every admin page and action verifies on the server.

## Architecture

- **Two apps, one direction.** Next.js server code (server components, server actions, route handlers) is the
  only caller of FastAPI. Wrappers in `src/lib/api/` call the generated SDK through `callBackend`, which sends
  `X-Internal-Key` (`BACKEND_INTERNAL_KEY`) and the user's `session` cookie. ESLint forbids importing
  `@/lib/api/generated*` outside `src/lib/api/**` and tests, and forbids `firebase-admin` and `stripe` in the
  frontend. The frontend has no database.
- **Auth flow.** Firebase client SDK signs in and returns an ID token; `verifyTokenAction`
  (`src/app/actions/auth-actions.ts`) sends it to `POST /v1/auth/session`; the backend verifies it with Firebase
  Admin, syncs the user, records login history and returns the session cookie value; the action sets the httpOnly
  `session` cookie (5 days). Verified email is enforced server-side from the signed claim by
  `src/lib/auth/verified-session.ts`. The backend owns users, sessions, login history and 2FA.
- **Backend layering** (ADR-004): `app/api/v1` routers (HTTP only) > `app/services` (business rules,
  transactions) > `app/repositories` (SQLAlchemy, one aggregate each). `app/core` holds config, logging, errors,
  rate limiting, the database seam and Sentry. Never import upward.
- **Data.** Platform models in `app/models/platform/`; one Alembic baseline `0001`, ids from
  `app.core.ids.new_id`. Register new revisions in `PLATFORM_REVISIONS` (`app/core/platform_schema.py`).
  `GET /health/ready` answers 503 while migrations are pending. No deploy runs migrations.
- **Organisations.** Users belong to organisations through memberships (owner, admin, staff). Plan limits and
  subscription state live on the organisation, never on the user.
- **Server actions** (`src/app/actions/`): `"use server"`, validate with Zod, call `src/lib/api` wrappers,
  return `{ success, data?, error? }`, `revalidatePath()` after writes. No backend or provider text reaches the
  browser: map error codes to curated messages.
- **Routes.** `(auth)/` login, signup, reset-password, verify-email; `(admin)/admin/` users, audit-logs, seo,
  settings, subscriptions, analytics; `dashboard/` the signed-in area; `pricing/`; `api/` health and auth helpers.
- **Logging.** Winston at `src/lib/logger.ts` (daily rotating files in `logs/`, excluded from webpack watching);
  backend structured logging in `app/core/logging.py`. Never log tokens, headers, payloads or secrets.
- **Rate limiting** (ADR-007): Upstash Redis in the backend (`app/core/rate_limit.py`, policies per route,
  fail-closed for sign-in). Local dev and tests use the disabled limiter.
- **Modules.** Stripe Billing and Capacitor are optional; `docs/developer/modules.md` lists every file they own.
  Keep module code behind the touch points listed there so it stays removable.

## Environment variables

Every variable is documented in `nextjs-frontend/.env.example` and `fastapi_backend/.env.example` (names and
placeholders only; never commit values). `BACKEND_INTERNAL_KEY` must match between the two apps. Never put a
secret in a `NEXT_PUBLIC_*` variable. The brand name is `NEXT_PUBLIC_APP_NAME` (frontend, read by
`src/config/site.ts`) and `APP_NAME` (backend).

## Testing notes

- Coverage thresholds are ratchets, not the 80% project minimum. Frontend (`vitest.config.ts`): statements 90,
  branches 88, functions 88, lines 91. Backend (`pyproject.toml`): `fail_under = 91.0`. Each sits about 1 to 2
  points under the measured figure (frontend 91.5 / 90.3 / 89.7 / 92.3; backend 92.3 without the database, 98.6
  with it; FREEJ-65 moved the backend figures up by changing the coverage tracer, see `docs/developer/testing.md`). Raise a threshold when measured coverage rises; never lower one.
- The backend floor is set against the database-less run, the lower of the two. CI and `scripts/gate.sh` run the
  suite with a Postgres (`*_e2e.py` included) and `gate.sh` without `GATE_TEST_DATABASE_URL` runs it
  database-less; both must pass `fail_under`.
- Backend tests use fakes (`tests/fakes.py`) and need no database; `*_e2e.py` tests run only with
  `TEST_DATABASE_URL` (a local database whose name ends in `_test`; they rebuild it with Alembic). CI provides one.
- Playwright: `npm run test:setup` creates test accounts in a separate Firebase test project; `test:e2e:local`
  builds a production bundle and starts it (never `next dev`). See `docs/developer/local-e2e.md`.
- CI: only the `CI gate` check matters. E2E is not in the gate; run specs locally and record the result in the PR.

## Working rules

- Conventional commits (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`, `ci:`), branches
  `<type>/<short-slug>`. Pull request template in `.github/`.
- Backend route or schema change: regenerate `openapi.json` and the client in the same PR; never hand-edit
  `src/lib/api/generated/`.
- One Alembic head: a second migration branch must rebase, regenerate and get a green `CI gate` before merge.
- Run `uvx pre-commit run --from-ref origin/main --to-ref HEAD` before opening a PR.
- `.claude/hooks/guard.mjs` blocks force-pushes, pushes to `main`, merges without `--match-head-commit` and
  publishing the package to the npm registry.
