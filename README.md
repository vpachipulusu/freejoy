# FastAPI + Next.js boilerplate

A production-shaped starter for a SaaS web app: a **Next.js 16** frontend and a **FastAPI** backend, with
Firebase authentication, organisations and memberships, an admin panel, audit logging, 2FA, rate limiting,
security headers, Sentry, structured logging, and optional **Stripe Billing** and **Capacitor** modules.
Everything is tested, linted and type-checked in a path-filtered GitHub Actions pipeline that ends in one
required check, `CI gate`.

The brand name ("Freejoy" in the examples) is set in two env files (see [Branding](#branding)).

## What you get

| Area | Frontend (`nextjs-frontend/`) | Backend (`fastapi_backend/`) |
| --- | --- | --- |
| Auth | Firebase client SDK sign-in (email, Google), httpOnly session cookie, route protection in `src/proxy.ts` | Firebase Admin verification, session create/refresh/revoke, login history, TOTP 2FA |
| Accounts | Profile, password change, notifications, data export, account deletion | `/v1/me`, `/v1/me/export`, `/v1/notifications`, `/v1/totp` |
| Organisations | Membership-aware billing and plan usage | `/v1/organizations` with roles (owner, admin, staff), plan limits |
| Admin | Users, audit log, settings (theme, fonts, feature flags), subscriptions, analytics | `/v1/admin/*` guarded by permissions, audit log on every change |
| Platform | Theme and font settings, Winston logging, Sentry, rate limiting, security headers, CORS | Structured logging, Sentry, Upstash rate limits (ADR-007), request ids, health and readiness |
| Data | Typed client generated from the backend's OpenAPI schema (ADR-008) | SQLAlchemy async + Alembic (single baseline), layered routers > services > repositories (ADR-004) |
| Modules | Stripe Billing (pricing page, checkout, billing portal, admin subscriptions) and Capacitor mobile shell | Stripe webhook, org billing, plan prices, admin subscription sync |

The browser never calls FastAPI. Next.js server code calls it with an internal service key
(ADR-003). Design decisions are recorded in [`docs/adr/`](docs/adr/README.md).

## Quick start

Prerequisites: Node 22, Python 3.12, [uv](https://docs.astral.sh/uv/), Docker (for Postgres), a Firebase
project (Authentication enabled; a service account for the Admin SDK).

```bash
# 1. Database
docker compose up -d db           # Postgres 16 on localhost:5432 (fake local credentials from .env.example)

# 2. Backend
cd fastapi_backend
cp .env.example .env              # fill in the Firebase Admin values; the rest works locally as is
uv sync
uv run alembic upgrade head
uv run fastapi dev app/main.py --host 127.0.0.1 --port 8000

# 3. Frontend (new terminal)
cd nextjs-frontend
cp .env.example .env.local        # fill in the Firebase client and Admin values and BACKEND_INTERNAL_KEY
npm ci
npm run dev                       # http://localhost:3000
```

`BACKEND_INTERNAL_KEY` must be the same value in both env files (any random string locally). Full setup,
every variable and the Docker alternative: [`docs/developer/getting-started.md`](docs/developer/getting-started.md).

The whole stack also runs with `docker compose up` (frontend, backend, Postgres, Redis); `scripts/compose-smoke.sh`
is the health check CI runs against it.

## Everyday commands

Frontend (from `nextjs-frontend/`): `npm run lint`, `npm run typecheck`, `npm run test:unit`, `npm run build`,
`npm run test:e2e:local -- <spec>` (Playwright against a production build; needs a running backend).

Backend (from `fastapi_backend/`): `uv run ruff check . && uv run ruff format --check .`, `uv run mypy`,
`uv run pytest` (no database needed; `TEST_DATABASE_URL` enables the Postgres-backed tests), and after any
route or schema change `uv run python -m commands.generate_openapi_schema` then `npm run generate:client`.

Pre-commit runs the same checks on changed files: `uvx pre-commit run --from-ref origin/main --to-ref HEAD`.

## Branding

The brand name lives in one place per app, both read from env:

- `nextjs-frontend/.env.example`: `NEXT_PUBLIC_APP_NAME`, `NEXT_PUBLIC_APP_URL`, `NEXT_PUBLIC_SUPPORT_EMAIL`
  (read by `src/config/site.ts`, which everything else imports).
- `fastapi_backend/.env.example`: `APP_NAME`.

Search for `yourapp` to find the remaining identifiers (compose project name, database names, logger names)
and rename them if you want to.

## Optional modules

[`docs/developer/modules.md`](docs/developer/modules.md) lists every file, env variable, route and CI step
that belongs to each module and how to remove it cleanly:

- **Stripe Billing**: subscription tiers per organisation, Checkout, Billing Portal, webhook, admin
  subscriptions and analytics.
- **Capacitor**: iOS and Android shells around the web app.

## Starting a project from this template

1. Click **Use this template** on GitHub (or clone and `git init` a fresh history).
2. Follow [Quick start](#quick-start), then rename the brand ([Branding](#branding)).
3. Remove the modules you do not need ([`modules.md`](docs/developer/modules.md)).
4. Turn on branch protection for `main` requiring the `CI gate` check; the workflows and Dependabot config
   are ready.
5. Read [`docs/developer/workflow.md`](docs/developer/workflow.md) for the day-to-day development loop and
   [`.claude/CLAUDE.md`](.claude/CLAUDE.md) if you work with coding agents.

Keeping a project in step with this template later: [`docs/developer/template-sync.md`](docs/developer/template-sync.md).

## Known gaps

- The backend exposes `POST /v1/organizations`, but the frontend ships no page to create or manage an
  organisation; billing and plan usage assume the signed-in user already belongs to one. Add a settings page
  that calls the organisations API, or create organisations server-side on first sign-in.
- Transactional email is not wired. Firebase handles verification and password-reset mail; anything else
  (notifications, invites) needs a provider of your choice in the backend.
- Playwright e2e runs locally only. `.github/workflows/e2e.yml` exists but is disabled until a Firebase test
  project and secrets are configured.

## Documentation

- [`docs/user-guide/README.md`](docs/user-guide/README.md): the end-user guide with screenshots (public site and admin
  area), written for non-developers. Also as one Word file:
  [`docs/user-guide/Freejoy-User-Guide.docx`](docs/user-guide/Freejoy-User-Guide.docx).
- [`docs/developer/README.md`](docs/developer/README.md): index of developer docs (architecture, auth,
  organisations, billing, testing, security, local e2e).
- [`docs/adr/README.md`](docs/adr/README.md): architecture decision records and the ADR template.

## License

MIT, see [`LICENSE`](LICENSE). The backend started from
[vintasoftware/nextjs-fastapi-template](https://github.com/vintasoftware/nextjs-fastapi-template) (MIT);
its notice is kept in [`fastapi_backend/TEMPLATE_LICENSE.txt`](fastapi_backend/TEMPLATE_LICENSE.txt).
