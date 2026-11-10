# Backend: the partner surface

The partner API is how retailers will submit offers to Freejoy. This page covers what exists after FREEJ-56 and
FREEJ-57: the switched-off surface and its confinement tests, **per-merchant API keys, partner authentication,
rate limits and the restricted database role**, with one harmless endpoint (`GET /partner/v1/me`) that proves the
whole path. No offer can be touched yet (FREEJ-58 and FREEJ-59). The reasons are in
[ADR-013](../adr/0013-partner-api.md).

## What exists

| Piece | Where |
| --- | --- |
| The sub-app (no docs, no schema route, no slash redirects, the shared error envelope) | `app/partner/app.py` (`create_partner_api`) |
| The wrapper that mounts it: switch, body cap, headers | `app/partner/surface.py` (`PartnerSurface`) |
| The mount: first route of the app, matches by string comparison, also takes the bare prefix `/partner/v1` | `app/partner/mount.py` (`PartnerMount`), installed by `install_partner_surface` (`app/partner/app.py`) from `app/main.py` |
| The switch | `PARTNER_API_ENABLED` in `app/core/config.py` (default `false`; `.env.example`) |
| The partner OpenAPI document | `docs/partner/openapi.json`, written by `commands/generate_partner_openapi_schema.py` |
| Its freshness check | `commands/check_partner_openapi_schema.py` (pre-commit and `scripts/gate.sh`) |
| The public route | the rewrite `/partner/v1/(.*)` in the root `vercel.json`, before the catch-all |
| Post-deploy smoke | `scripts/smoke-partner-surface.sh`, the `partner-surface` job of `.github/workflows/post-deploy-smoke.yml` |
| Key format, parsing, hashing | `app/core/partner_keys.py` |
| Table `partner_api_keys` | `app/models/platform/partner_api_key.py`, migration `0007` |
| Admin API (list, issue, revoke) | `app/api/v1/admin_partner_keys.py`, `app/services/admin_partner_keys.py`, `app/repositories/admin_partner_keys.py` |
| Admin UI | the merchant page: `nextjs-frontend/src/app/(admin)/admin/merchants/_components/` (`partner-keys-section.tsx`, `create-partner-key-dialog.tsx`, `revoke-partner-key-button.tsx`), actions in `src/app/actions/admin/partner-key-actions.ts`, wrappers in `src/lib/api/admin-partner-keys.ts` |
| Authentication | `app/partner/auth.py` (`PartnerAuthenticator`, run by the surface; `require_partner_key` and `PartnerContext` on the sub-app) |
| Key lookups | `app/partner/keys.py` (SQLAlchemy Core definitions of the two tables the partner reads; no ORM models) |
| Rate limits | `app/partner/limits.py` (the sub-app dependency), policies in `app/core/rate_limit.py` |
| Restricted role | `commands/sql/partner_role.sql`; `PartnerSessionProvider` in `app/core/database.py`; `app/partner/deps.py` |
| The endpoint | `GET /partner/v1/me` in `app/partner/routes.py` |

`app/partner/` imports only `app.core` and itself, never `app.api`, `app.services`, `app.repositories` or `app.models` ([ADR-004](../adr/0004-backend-layering.md)). That is why the key format, the rate-limit enforcement helpers and the rate limiter singleton live in `app/core` (`partner_keys.py`, `rate_limit_enforcement.py`, `rate_limiter_provider.py`; `app.api.rate_limit` and `app.api.deps` re-export them), and why `app/partner/keys.py` names its two tables with SQLAlchemy Core instead of importing the models (`tests/api/partner/test_partner_table_definitions.py` checks every table and column it names exists in the ORM metadata).

## What a caller sees

Every response under `/partner/v1`, errors and 404s included:

- `Cache-Control: no-store, private`, `X-Content-Type-Options: nosniff`, `X-Freejoy-Surface: partner`;
- never `Set-Cookie`, and no `Access-Control-*` header, including on `OPTIONS` (a retailer calls from a server);
- the usual envelope, `{"success": false, "error": "<fixed text>"}`. The messages are a closed set (`Unauthorized`,
  `Not Found`, `Method Not Allowed`, `Payload too large`, `Invalid request`, `Too many requests`, `Internal server
  error`, `Service temporarily unavailable`) and `tests/api/partner/test_partner_errors.py` checks every partner error
  body against it and that none echoes the path, a header, the key or the body. The shared error handler still passes a
  string `detail` and the exception's `headers` through (a 405 carries `Allow`), so a new handler is checked by that
  test, not by eye.

While `PARTNER_API_ENABLED` is `true`, **authentication comes before routing**: a request under `/partner/v1`
without a valid partner key gets the same `401 {"success": false, "error": "Unauthorized"}` (with
`WWW-Authenticate: Bearer`) whatever the path or method, unknown paths, `OPTIONS` and wrong methods included, so which
routes exist is not learned without a key. It also comes before the body is looked at, so an invalid body with no key is a
401, not a 422 or a 413. With a valid key an unknown path is the sub-app's own 404 and a wrong method its 405. A 429
(too many failed attempts) and a 503 (the database or the limiter is unavailable) are the only other answers before routing.

While `PARTNER_API_ENABLED` is `false` (the default) every request under the prefix answers the same 404 (the switch
is checked first, before any key is looked at): same body, same headers, no `Location`, whatever the request headers say (a large
`Content-Length` included: the switch is checked before the size cap). That includes `/partner/v1`, `/partner/v1/`,
`/partner/v1/docs`, `/partner/v1/openapi.json`, `/partner/v1/../v1/me`, `/partner/v1/%2e%2e/v1/me`,
`/partner/v1//x` and `/partner/v1/a%0Ab` (a newline). The app never normalises a path, so those stay inside the
prefix. `PARTNER_API_ENABLED=` (blank) means off; any other value that is not a boolean stops startup.

A request body over 256 KiB (`MAX_BODY_BYTES`) gets a 413 (after authentication), and **the surface owns that answer**. The cap is checked on
the declared `Content-Length` and again on the bytes actually received, so a missing or false length does not get
round it and the surface never buffers the body (Vercel may buffer it before the app sees it). Crossing the cap raises
a private `BaseException` from `receive`: a handler's `except Exception`, the sub-app's error middleware and
body-reading middleware cannot swallow, log or convert it, and whatever the application does next the client gets the
413. Once a response has started, `receive` reports a disconnect instead. So there is no rule about where in the
sub-app a body may be read. Raise the constant deliberately when a real endpoint needs more.

The headers, the switch and the cap are applied by `PartnerSurface`, a wrapper around the sub-app, not middleware
inside it: a FastAPI app's error middleware sits outside its own middleware, so a 500 would otherwise miss them.

## Keys

A key is `fj_<env>_<key_id>_<secret>` (`app/core/partner_keys.py`):

- `<env>` is the running environment (`VERCEL_ENV`, else `ENVIRONMENT`, as Sentry names it; lower case letters and digits,
  at most 16). A key issued in one environment is refused by every other.
- `<key_id>` is the 24-character id of the `partner_api_keys` row (`[a-z0-9]`, from `app.core.ids.new_id`). It selects the row
  and is not secret.
- `<secret>` is 32 bytes from `secrets` as 64 lower-case hex characters.

The alphabets have no `_` and the lengths are fixed, so one anchored ASCII pattern parses a key in exactly one way
(`fullmatch`: a trailing newline does not match) and a key longer than 160 characters is refused before the pattern runs.

**At rest** only `key_hash`, the SHA-256 of the whole key, is stored (precedent: `token_digest` in
`app/core/subscriber_tokens.py`). A plain hash is enough because the secret is 256 bits from a CSPRNG: there is nothing to
guess, and nothing for a slow hash or a salt to protect (a password would need both). The key is shown **once**, in the
response that creates it, and is never stored, logged or audited. Lookup is by `key_id` (a primary-key read) and the
comparison is `hmac.compare_digest`; for an unknown `key_id` the same comparison runs against a dummy digest, so the work
done does not tell a caller which ids exist.

Ownership is per merchant, not per organisation: `partner_api_keys.merchant_id` (`ON DELETE CASCADE`). A key stops
working when it is revoked (`revoked_at`) and when its merchant is deactivated. `last_used_at` moves on use, at most once
a minute per key (`LAST_USED_GRANULARITY_SECONDS` in `app/partner/keys.py`: it is a "last seen" hint, not a log, and a write
per request would sit on the hot path of every call). A revoked key keeps its row.

**Admin API** (the internal router, behind `X-Internal-Key` and an admin permission, like `admin_merchants`):

| Route | Permission | Notes |
| --- | --- | --- |
| `GET /v1/admin/merchants/{id}/partner-keys` | `merchants.read` | Never the key or its hash |
| `POST /v1/admin/merchants/{id}/partner-keys` | `merchants.write` | Body `{label}` (1 to 80 characters, one line, not key-looking). 201 with the key once. 409 `merchant_no_website` / `merchant_inactive`; 503 `keys_unavailable` where the environment cannot be written into a key |
| `POST /v1/admin/merchants/{id}/partner-keys/{key_id}/revoke` | `merchants.write` | A second revoke is a no-op that writes no audit row. A key of another merchant is a 404 |

Create and revoke are audited through `record_admin_action` (`admin.partner_keys.create`, `admin.partner_keys.revoke`,
resource `partner_api_keys`, resource id = the key id). The audit redaction (`app/services/audit.py`) knows the key prefix, so
a key pasted into any free-text field is `[REDACTED]`, and a field named like `key_hash` is redacted by name. The
permissions: `merchants.read` is a new entry in the closed registry (`app/core/permissions.py`); a dedicated
`partner_keys.*` permission was considered and not added (issuing a key is a merchant-level act; a super admin holds both
through `*`, anyone else needs `merchants.read` added to their grant).

**Admin UI**: on the merchant page, a keys list (label, created, last used, status), "Create key" (a dialog: label, then the
key once in a read-only field with a copy button and a polite status announcement; never in a toast, a log or the URL; it is
cleared when the dialog closes) and "Revoke" (a confirmation that names the key by its label).

## Authentication

`PartnerSurface` runs `PartnerAuthenticator` (`app/partner/auth.py`) for every request under the prefix while the switch is
on, before routing and before the body. The rules:

1. Only `Authorization: Bearer <key>` is read: one header, scheme in any case. Never a query parameter, a cookie or another header.
2. A key presented together with `X-Internal-Key`, `X-Session-Token` or a `Cookie` header is rejected, before any lookup.
   Partner code never calls `require_internal_key`, `get_session_identity` or the forwarded-client-IP helpers
   (`tests/api/partner/test_partner_isolation.py` fails on those names in any partner module).
3. No header, a malformed key, a key for another environment, an unknown id, a wrong secret, a revoked key, an inactive
   merchant and a mixed-credential request all give the same 401: same body, same headers. The cause is logged with the key
   id only (never the key, the header or the hash).
4. A valid key gives a frozen `PartnerContext(key_id, merchant_id)`, stored in the scope. `require_partner_key` is the dependency
   attached to the sub-app itself (`create_partner_api`): it hands routes that context and answers 401 if there is none. A route
   takes `PartnerContextDep` and scopes every query to `context.merchant_id`.
5. A database failure during authentication is a 503 (never a 500 or a 401); a failed `last_used_at` update does not refuse a
   valid key.

The sub-app's lifespan and startup hooks never run under a mount, so nothing relies on them: the partner session provider is
built the first time it is asked for (`app/partner/deps.py`).

## Rate limits

Policies are in `app/core/rate_limit.py` ([ADR-007](../adr/0007-rate-limit-keys.md)), applied with the shared enforcement
helpers (`app/core/rate_limit_enforcement.py`). None is keyed by client address: partner code does not read client-IP headers.

| Policy | Key | Limit | On a Redis outage |
| --- | --- | --- | --- |
| `PARTNER_READ_POLICY` (`GET`, `HEAD`, `OPTIONS`) | `rl:partner_read:key:<key id>` | 120 per minute | **fails open** (log, Sentry, let through) |
| `PARTNER_WRITE_POLICY` (any other method) | `rl:partner_write:key:<key id>` | 30 per minute | **fails closed** (503) |
| `PARTNER_AUTH_FAILURE_POLICY` (a failed authentication) | `rl:partner_auth_failure:key:<presented key id>`, or `rl:partner_auth_failure:malformed` for a key that does not parse | 30 per minute | **fails closed** (503) |

The read and write limit is one dependency on the sub-app (`limit_partner_requests`) that picks the policy from the method, so a
route cannot forget it. Failures are counted only on failure: a valid key is never held up by them. Limitation: a caller that
rotates through new, well-formed key ids gets a bucket each, and the shared bucket for malformed keys is shared by everyone who sends one.
The 256-bit secret is what stops guessing; these limits bound database lookups and noise. The Vercel Firewall rate rule on
`/partner/v1/` (a launch precondition below) covers the rest.

## The restricted database role

Every partner route and the authentication use `PartnerSessionProvider` (`app/core/database.py`), bound to `PARTNER_DATABASE_URL`,
never the owner `DATABASE_URL`. `PARTNER_DATABASE_URL` is required when `PARTNER_API_ENABLED` is true (startup fails with a
clear message if it is missing, or equals `DATABASE_URL`) and is not needed while the switch is off. It is a secret in
`Settings`, documented in `.env.example` (placeholder only).

`commands/sql/partner_role.sql` creates the role `freejoy_partner` (the role name is a psql parameter) and grants exactly:
`SELECT` and `UPDATE (last_used_at)` on `partner_api_keys`, `SELECT` on `merchants`, `INSERT` (only) on `audit_logs`. It has no
access to users, admins, organisations, memberships, subscribers, billing, TOTP or login tables, and no DDL. It is idempotent: the role is created only if
absent and its table privileges are rebuilt from scratch each run. Later partner stories extend this file.

**How the owner creates it** (run as the database owner, after `alembic upgrade head`, and again after any story that adds a grant):

```bash
psql "$OWNER_DATABASE_URL" -v ON_ERROR_STOP=1 \
     -v partner_role=freejoy_partner -v partner_password="$PARTNER_ROLE_PASSWORD" \
     -f fastapi_backend/commands/sql/partner_role.sql
```

Then set `PARTNER_DATABASE_URL` in the backend's environment to the role's URL, ending `?sslmode=verify-full`. PostgreSQL 15
or newer is assumed. This is an owner step: nothing in a deploy runs it.

`tests/core/test_partner_role_e2e.py` (Postgres only) applies the script, connects as the role and checks it is refused on every other
table, on `UPDATE offers`, on `DELETE`/`UPDATE`/`SELECT` of `audit_logs`, and on DDL, and that the key lookup works. Roles are cluster-wide
and the test cluster is shared, so the test never touches a role called `freejoy_partner`: it uses `freejoy_partner_<database name>`
(`tests/partner_role_support.py`), created by the script if absent, never dropped, with grants only inside its own database.

## How confinement works

The invariant ([ADR-013](../adr/0013-partner-api.md)): a caller holding a valid partner key and the public
partner URL can invoke only partner handlers, and can read or change only its own merchant's data.

1. **Vercel** sends only `/webhooks/stripe` and `/partner/v1/(.*)` to the backend. Everything else, including
   `/partner/v1` without a slash, goes to the frontend.
2. **One mount, first, matched by string.** Partner handlers exist only inside the mount at `/partner/v1`, which is
   the first route of the app and matches `/partner/v1` and everything below it by string comparison (Starlette's regex
   mount misses a newline), on the full path as well as the path below a `root_path`. No main-app route starts with
   `/partner`, in any letter case, or can match a partner path whatever its pattern.
3. **The internal key.** Every main-app route needs `X-Internal-Key` ([ADR-003](../adr/0003-nextjs-to-fastapi-authentication.md)),
   apart from `/health` and `/webhooks/stripe`, which have their own rules, and the docs routes where they are enabled.
4. **A distinct partner credential, checked before routing** (FREEJ-57): `Authorization: Bearer` only; a partner key is refused
   by every internal route (which asks for `X-Internal-Key`), and the internal key is refused by every partner route; a request
   carrying both kinds is rejected. See *Authentication*.
5. **A restricted database role** (FREEJ-57) reached through its own session provider. See *The restricted database role*.
6. **Scoping to the key's merchant** (planned, FREEJ-58): `PartnerContext.merchant_id` is there; no route uses it yet.

Layers 1 to 5 are proved by tests that run in the normal suite (layer 5 needs Postgres).

One thing the surface cannot undo: middleware added to the **main** app runs outside it and could add CORS headers
or cookies to partner responses. The main app has only the request-ID middleware, and a test pins that list. If
you need to add middleware to `app/main.py`, that test will fail; decide there how `/partner/v1` is treated.

## Adding an endpoint (for FREEJ-58)

1. Add the route to `app/partner/routes.py` (or a new module in `app/partner/` whose router is included in
   `create_partner_api`). Paths are written relative to the mount (`/offers`, not `/partner/v1/offers`).
2. Do **not** attach authentication or a rate limit: both are on the sub-app. Take `PartnerContextDep` and scope every
   query to `context.merchant_id`. Get the session from `app/partner/deps.py` (the partner session), never the owner's.
3. If the route needs a table, add it to `app/partner/keys.py` (or a sibling) with SQLAlchemy Core, add the grant to
   `commands/sql/partner_role.sql`, extend `tests/core/test_partner_role_e2e.py`, and tell the owner to re-run the script.
4. Regenerate the partner schema (below). The generator writes the full path, `/partner/v1/offers`.
5. Run the proofs. They must stay green; `test_partner_credentials.py` generates its cases from the real routes, so a new
   route is covered automatically.

## The partner schema

`docs/partner/openapi.json` is the retailer-facing contract. It is a separate file from
`nextjs-frontend/openapi.json` (which feeds the generated client and must never contain a `/partner` path). It
lives under `docs/` because nothing in the frontend uses it and it is what gets shared with retailers. Every path in
it starts with `/partner/v1/`. With no endpoints it has no paths.

```bash
cd fastapi_backend
uv run python -m commands.generate_partner_openapi_schema   # writes ../docs/partner/openapi.json (LF)
uv run python -m commands.check_partner_openapi_schema      # fails when the committed file is stale
```

The partner contract has its own title and version (`PARTNER_API_TITLE` and `PARTNER_API_VERSION` in
`app/partner/app.py`), separate from the internal API ([ADR-008](../adr/0008-api-client-generation.md) amendment).

## Running the proofs

All run under plain `uv run pytest` (no database needed); `scripts/gate.sh` carries them.

```bash
cd fastapi_backend
uv run pytest tests/core/test_vercel_routing.py tests/api/test_route_confinement.py \
  tests/api/partner tests/commands/test_partner_openapi_schema.py
```

| Test file | Proves |
| --- | --- |
| `tests/core/test_vercel_routing.py` | The real `vercel.json` sends only the Stripe path and the partner prefix to the backend; `/partner/v1`, `/partner/v2/x`, `/partnerx/v1/x`, `/v1/me`, `/openapi.json` and `/docs` go to the frontend |
| `tests/api/test_route_confinement.py` (walker: `tests/confinement_walker.py`) | One mount, a `PartnerMount` around a `PartnerSurface`, first; every main-app route needs the internal key or is allowlisted (and answers 401 without it); no main-app route under `/partner` or able to match it (each route's own regex is probed); only Stripe is reachable by a backend rewrite; no dependency overrides; routes inside the mount are defined in `app.partner`, shared with no main-app route, and do not use the internal key. Negative tests show the walker fails on bad apps |
| `tests/api/partner/test_partner_surface.py`, `test_partner_mount.py` | The same 404 for adversarial paths (newline, dot segments, encoded slashes, `//`, a `root_path` of the prefix), the exact header set on every response, no CORS on `OPTIONS`, the switch, one error log per failing request, the mount matching and its position |
| `tests/api/partner/test_partner_auth_attached.py` | The auth and rate-limit dependencies are on the sub-app; every partner route has `require_partner_key` in its tree; none has `require_internal_key`, `get_session_identity` or the client-IP helpers (negative tests show it fails on a bad sub-app) |
| `tests/api/partner/test_partner_credentials.py` | For every partner route and method: no credentials, the internal key only and a session token only are 401; a partner key with the internal key, a session token or a cookie is rejected; `request.state.internal_authenticated` is never set |
| `tests/api/partner/test_partner_auth.py` | One 401 for every bad key; the query string is never read; auth before the body (a bad body with no key is 401); failures counted per key id, 429 past the limit, 503 when the limiter is down; nothing secret in the logs |
| `tests/api/partner/test_partner_limits.py` | Read and write policies, keyed by key id, with their fail modes |
| `tests/api/partner/test_partner_errors.py` | Every partner error body is a fixed message and echoes nothing |
| `tests/api/partner/test_partner_table_definitions.py` | The Core table definitions match the ORM metadata |
| `tests/api/partner/test_partner_keys_e2e.py`, `tests/api/test_admin_partner_keys_e2e.py` (Postgres) | Create shows the key once; only the hash is at rest; revoke takes effect on the next request; `last_used_at` moves; a deactivated merchant's keys stop |
| `tests/api/test_partner_key_rejected_elsewhere.py` | For every main-app route, a valid partner key and nothing else never gives a 2xx and never carries the partner surface header (the open routes are held to "the key changes nothing") |
| `tests/core/test_partner_role_e2e.py` (Postgres) | The restricted role can do what the script says and nothing else |
| `tests/api/partner/test_partner_body_cap.py` | The 413 cannot be swallowed, converted or turned into a 500 by handlers, middleware or streaming responses |
| `tests/api/partner/test_partner_isolation.py` | `app/partner` imports only `app.core` and `app.partner`, and names no internal credential, client-IP helper or owner session; the mounted sub-app has the routes the schema is generated from |
| `tests/commands/test_partner_openapi_schema.py` | The partner schema is current and under the prefix; the internal schema has no partner path |

**Raw paths in tests.** An HTTP client (httpx) tidies `..` and `//` before it sends, so it cannot send the
adversarial paths. `tests/api/partner/support.py` builds the ASGI scope by hand (`path` decoded, `raw_path` as on
the wire) and calls the application directly. `Recorder` notes the `path` that reached the app, and each test checks
it is unchanged. `scripts/smoke-partner-surface.sh` sends the same paths over a real HTTP connection
(`curl --path-as-is`).

**Vercel matching.** `tests/vercel_rewrites.py` matches paths as Vercel documents (rewrites in order, first match
wins, `source` is case-sensitive, a service receives the original path). It also assumes, and cannot verify
offline, that `source` is path-to-regexp with a literal path and `(.*)` only, that a trailing slash matters and that
`(.*)` can match nothing. A rewrite with other syntax, or with a `has`/`missing` condition, makes it raise instead of
guessing.

## Launch preconditions (owner steps)

Not done by this story. Recorded in [ADR-013](../adr/0013-partner-api.md).

1. Remove `API_DOCS_ENABLED` from the production backend and redeploy. It serves the whole internal schema with no
   key.
2. Turn on Deployment Protection for preview deployments.
3. Add a Vercel Firewall rate rule on `/partner/v1/`. The rewrite sends every such request to the backend function, which
   answers the 404 itself, so unauthenticated traffic costs function time and shares concurrency with the internal API
   even while the surface is off.
4. Check the first production run of the `partner-surface` smoke job. It needs no secret and passes whether the
   surface is off (every path is the same 404) or on (authentication comes before routing, so a "must" path answers 404 or 401, always with the
   surface header). If it fails, the backend answered a path under or around the prefix without the surface header:
   read its log before changing anything. It is also a manual step: `bash scripts/smoke-partner-surface.sh https://<domain>`.
5. Create the `freejoy_partner` role from `commands/sql/partner_role.sql` (see *The restricted database role*) and set `PARTNER_DATABASE_URL` (ending `?sslmode=verify-full`). Startup refuses `PARTNER_API_ENABLED=true` without it.
6. Leave `PARTNER_API_ENABLED` unset in production until FREEJ-58 and FREEJ-59 are in.

The smoke is a workflow job rather than a manual step because it can run without secrets and cannot break while the
surface is off or on (it sends no key). It speaks https only, validates the base URL, follows redirects once (on the root, to find the
effective origin) and never on a probe. Whether the real Vercel edge treats the `..`, `%2e%2e`, `%0A` and `//` paths the
way the script assumes has not been seen yet; it insists on the surface header and `Cache-Control` only for paths that
cannot leave the prefix, and for the others fails only on a backend answer (`X-Request-ID` present, or a JSON error
envelope) without the surface header. That the Next.js frontend never sets `X-Request-ID` is checked in this repository
(`src/proxy.ts`, `next.config.ts`); that Vercel does not add one is not verified.

## Not built yet

The table `partner_submissions` and every endpoint beyond `GET /partner/v1/me` (FREEJ-58 and FREEJ-59). Submissions will create
drafts only. Scoping routes to `PartnerContext.merchant_id` starts with the first route that reads or writes merchant data.
The hostname (`partners.<domain>` or the path on the main domain) is an open question, set out in ADR-013.
