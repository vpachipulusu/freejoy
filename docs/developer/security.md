# Security

**Last updated:** 2026-10-12

## Server actions are public endpoints

Every exported Next.js server action is a public POST endpoint. Anyone can call it with any argument, outside the UI. Route handlers are public in the same way.

- Authenticate and authorise **inside each action or handler**. Do not rely on the page or the UI.
- Never trust a client-supplied tier, role or plan. Read them from the verified session or the database.
- Validate every input with a schema (Zod in the frontend) before using it.
- Return the `{ success, data?, error? }` shape and do not leak internal errors.

The unauthenticated auth actions and the `/api/auth/signup`, `/api/auth/login` and `/api/auth/logout` route handlers are removed, and the client-supplied plan argument is gone. The `auth-actions-security` unit tests cover this.

## Server secrets and database access

- The frontend holds no `DATABASE_URL`, no Firebase Admin credentials and no Stripe secret key. FastAPI owns them.
- The frontend has no database client, no Firebase Admin SDK and no Stripe server SDK: ESLint blocks `stripe`, `firebase-admin`, `@/lib/firebase/admin` and `@/lib/stripe/server`, and the frontend CI workflow greps for server-only packages.
- The frontend calls FastAPI from the server only, with the internal service key `BACKEND_INTERNAL_KEY`, sent as the `X-Internal-Key` header (ADR-003). Use a separate key per environment (ADR-011). The key is itself a server secret: it is set only on the Next.js server, never in a `NEXT_PUBLIC_*` variable.
- Never put a secret in a `NEXT_PUBLIC_*` variable; anything with that prefix is bundled into the browser.

## Credentials

- Never print or commit credentials, tokens or keys. Do not paste them into tickets, PRs, chat, or logs.
- `.env`, `.env.local` and `.env*.local` are gitignored (root `.gitignore`). `.env.example` is the only committed env file, and it contains names and placeholders only.
- Store real values in the team's secret store. Validate required secrets at startup.
- If a secret is exposed, rotate it immediately.

## Partner API keys

The partner API ([ADR-013](../adr/0013-partner-api.md), [backend-partner-api.md](backend-partner-api.md)) is the one public caller of the backend that
is not our Next.js server and not a signed-in user. Rules for keys:

- A key is `fj_<env>_<key_id>_<secret>` with a 256-bit secret from `secrets`. It is shown once, in the response that creates it, and is
  never stored (only its SHA-256), logged, audited, put in a toast or a URL. The backend logs the key id only, never the `Authorization`
  header, the key or its hash. The audit redaction recognises the key prefix, so a key pasted into free text is redacted.
- Only `Authorization: Bearer` carries it. A key sent together with `X-Internal-Key`, `X-Session-Token` or a `Cookie` is rejected. A partner
  key opens nothing outside `/partner/v1` (a test calls every main-app route with one (apart from `/health`, which is open and answers from the database)).
- Authentication comes before routing and before the body, so an unauthenticated caller learns nothing about which routes exist. A request
  with no header or an unparseable key never reaches the database and is never rate limited.
- The failure limit does not bound database work: every well-formed key costs a connection and a `SELECT`. Only the Vercel Firewall rate rule
  on `/partner/v1/` (a condition of `PARTNER_API_ENABLED=true`) and the role's connection limit bound unauthenticated lookups.
- The partner surface connects to the database as the restricted role `freejoy_partner` (`PARTNER_DATABASE_URL`), never the owner. Create it
  from `fastapi_backend/commands/sql/partner_role.sql` (an owner step, on the direct URL, with the password from the environment); the column grants
  are listed in that file, verified from the catalog by the script itself and proved by a Postgres test. Through `PUBLIC` the role still reads the
  catalogs, creates temporary tables and takes advisory locks: "no DDL" means no DDL in `public`. Its timeouts stop accidents, not injected SQL.
  The surface refuses to serve if the live connection is privileged or can read `users`.
- Issuing and revoking keys needs `partner_keys.write` (listing needs `partner_keys.read`), not `merchants.write`.
- Treat a leaked key as compromised: revoke it on the merchant page (it stops on the next request) and issue another.

## E2E test accounts

- `npm run test:setup` generates random emails and passwords, and writes them only to `.env.e2e.local`.
- E2E creates its accounts only in a separate Firebase test project, never in the production project.
- The setup script refuses to run against a non-local `DATABASE_URL` and refuses to touch any account whose email does not match the generated pattern.

## Reporting a security issue

If you find an issue, stop, use the security review step in the team's rules, fix any critical issue before continuing, and review the codebase for similar problems.
