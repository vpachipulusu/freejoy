# ADR-011: One Vercel project with services

- **Status:** Accepted by the product owner, 2026-10-12
- **Date:** 2026-10-11
- **Amended by:** [ADR-013](0013-partner-api.md) (a second public backend rewrite, `/partner/v1/(.*)`)
- **Supersedes:** an earlier two-Vercel-projects decision (not kept in this template)

## Context

An earlier decision chose two Vercel projects: one rooted at `nextjs-frontend/`, one at `fastapi_backend/`. That gives the
backend its own public URL, so ADR-003 had to protect it with an internal service key, and preview deployments
of the two projects had to be paired by hand.

Vercel Services (beta) deploys several services from one repository as one project with one domain. A service
gets no public traffic unless a top-level rewrite points at it, and one service reaches another over a
**binding**: an internal URL injected into the caller at runtime. That URL is per deployment, so a preview
frontend always calls the same preview's backend.

## Decision

Deploy Your App as **one Vercel project** with two services, configured in the root `vercel.json`:

| Service | Root | Public? | Notes |
|---|---|---|---|
| `nextjs-frontend` | `nextjs-frontend/` | Yes, catch-all `/(.*)` | `npm run build` |
| `fastapi_backend` | `fastapi_backend/` | **No public route** | FastAPI entrypoint `app.main:app` |

- **Binding:** `nextjs-frontend` → `fastapi_backend`, injected as `BACKEND_API_URL`. The server-only API
  client builds every backend URL from it. Vercel sets it; it is never set by hand on Vercel. For
  local development without `vercel dev`, set `BACKEND_API_URL=http://127.0.0.1:8000`.
- Bindings resolve **at runtime in functions only**: not during `next build`, and **not in `src/proxy.ts`**
  (middleware). Server components, server actions and route handlers call the backend; the proxy never does.
- **The backend stays internal.** When a backend endpoint must be public, such as provider webhooks
  (Stripe) or a health check for the release workflow, it gets its own narrow top-level
  rewrite placed before the catch-all, for example `/backend/webhooks/(.*)`. The service receives the
  **original path**, so FastAPI mounts those routes under the same prefix.
- **ADR-003 still applies.** A binding grants reachability but does not authenticate. The `X-Internal-Key`
  service key and the forwarded session stay, as defence in depth and for any public backend route.
- **Migrations are not part of the build.** The template's `fastapi_backend/vercel.json` (which ran
  `alembic upgrade head` on every build, previews included) and `vercel.prod.json` are removed. Platform
  migrations run from the release workflow on production deploys only.
- **Environment variables** are set on the one project, per environment. Server secrets go only where they are
  needed, and preview deployments use a separate database.

## Alternatives considered

- **Two projects.** Proven and not in beta, but it needs a public backend URL, a CORS/edge story
  for it, and manual pairing of preview deployments.
- **Next.js route handlers only, no separate backend.** Rejected in ADR-001.

## Consequences

- One domain and one Deployment Protection / Firewall setup; the backend has no public attack surface by
  default.
- Full-stack preview deployments work without extra wiring.
- Services is a **beta** feature. If it blocks something (for example Python runtime limits or binding
  behaviour), falling back to two projects means adding a public backend rewrite or second project and
  pointing `BACKEND_API_URL` at it. Application code does not change.
- Local development: `vercel dev` runs both services and injects the binding. Running the apps separately
  still works with `BACKEND_API_URL` set by hand.
