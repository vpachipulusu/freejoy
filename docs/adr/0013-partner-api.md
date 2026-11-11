# ADR-013: Partner API inside the backend

- **Status:** Proposed (the decisions below were made by the product owner; this record awaits sign-off; updated by FREEJ-57)
- **Date:** 2026-11-10
- **Deciders:** product owner (pending)
- **Amends:** [ADR-003](0003-nextjs-to-fastapi-authentication.md), [ADR-004](0004-backend-layering.md),
  [ADR-007](0007-rate-limit-keys.md), [ADR-008](0008-api-client-generation.md),
  [ADR-011](0011-single-vercel-project-with-services.md)

## Context

Retailers will later submit offers to Freejoy through an API (FREEJ-57 to FREEJ-59). That makes them a caller
that is not our Next.js server and not a signed-in user, reaching the backend from the internet.

A separate partner project was considered and decided against: the owner wants one repository, one deployment
and one set of models. So partner endpoints live inside `fastapi_backend`. The risk this creates is the one this
record manages: a retailer who holds a partner URL and a key must reach partner endpoints and nothing else.

FREEJ-56 built the **empty, switched-off surface** and the proofs that keep it confined. FREEJ-57 added **per-merchant keys,
partner authentication, the rate limits and the restricted database role**, with one harmless endpoint, `GET /partner/v1/me`,
that proves the path. FREEJ-58 added offer submissions that only produce drafts; FREEJ-59 adds clicks.

## Decision

### The invariant

> A caller holding a valid partner key and the public partner URL can invoke only partner handlers, and can
> read or change only its own merchant's data.

### The mechanism

- **A mounted sub-app at a fixed prefix.** The partner API is a second FastAPI app, mounted once at
  `/partner/v1` (`app/partner/`, installed by `install_partner_surface` in `app/main.py`). Nothing else in the
  backend is served under `/partner`, in any letter case. The version is part of the prefix; the partner contract
  is versioned on its own.
- **The mount is the first route and matches by string comparison** (`PartnerMount`). Starlette's own mount
  matches a regular expression in which `.` does not match a newline, so `/partner/v1/a%0Ab` would leave it and
  be answered by the main app without the surface headers; it also leaves the bare `/partner/v1` to the router
  (a 307). The string match takes both, and compares the full path as well as the path below a `root_path`, so a
  runtime that serves the app under `root_path=/partner/v1` cannot slide `/partner/v1/health` into a main-app
  route. Being first, no main-app pattern such as `/{a}/v1/{b}` or `/{rest:path}` is tried before it.
- **Authentication before routing, and attached at the sub-app** (FREEJ-57). `PartnerSurface` runs `PartnerAuthenticator` for every
  request under the prefix while the switch is on, before routing and before the body, so an unknown path, a wrong method or a raw route gets
  the same 401 as a real one and nothing about which routes exist is learned without a key (a FastAPI dependency alone does not run for a
  404 or 405). While the switch is off the answer is the same 404 as before: the switch is checked first. `require_partner_key` is also a
  dependency of the sub-app itself: it hands each route the `PartnerContext` the surface established, and a route added to the sub-app
  cannot forget it.
- **A credential and a store distinct from `X-Internal-Key`** (FREEJ-57). A partner key is never accepted by an
  internal route and the internal key is never accepted by a partner one. A request that carries a partner key together with
  `X-Internal-Key`, `X-Session-Token` or a `Cookie` is rejected, not resolved in favour of one. Unknown, revoked, malformed and wrong-environment
  keys all give the same 401.
- **No docs, no schema route and no CORS on the sub-app.** The partner contract is a committed file
  (`docs/partner/openapi.json`), not a served one. A retailer calls from a server, so no browser origin is allowed.
- **`Cache-Control: no-store, private`, `X-Content-Type-Options: nosniff` and `X-Freejoy-Surface: partner`** on
  every partner response, errors and 404s included. Never `Set-Cookie`.
- **The shared error envelope, with fixed messages.** Errors use `{"success": false, "error": "<message>"}`
  (ADR-008). The shared handler
  (`app/core/errors.py`) passes a string `detail` and the exception's `headers` through unchanged, so code that
  puts request text in a message or a header would echo it, and a 405 carries an `Allow` header. `tests/api/partner/test_partner_errors.py`
  (FREEJ-57) checks every partner error body (401, 404, 405, 413, 422, 429, 500, 503) against a closed set of fixed messages and that none
  echoes the path, a header, the key or the body.
- **A body-size cap that the surface owns** (256 KiB, `app/partner/surface.py`), by the declared `Content-Length`
  and again by counting the bytes actually received. A missing or false length is not trusted and the surface
  does not buffer the body. Crossing the cap raises a private `BaseException` from `receive`, so application code
  and the sub-app's error middleware cannot swallow, log or convert it; whatever the application then does, the
  client gets the 413 (the application's first message is replaced by it). After a response has started `receive`
  reports a disconnect instead. So there is no rule to follow about where the body may be read: a route, a
  dependency or middleware in the sub-app all get the same 413.
- **A switch, `PARTNER_API_ENABLED`, default off.** While it is off every request under `/partner/v1` answers
  the same 404, with the same body and headers, as an unknown path does while it is on, and that check comes
  before the body-size check, so no header (a large `Content-Length` included) changes the answer. A blank value
  means off; any other value that is not a boolean stops startup.

The surface headers, the body cap and the switch are applied by a pure ASGI wrapper (`PartnerSurface`) placed
*around* the sub-app, not as middleware inside it. A FastAPI app's error middleware sits outside its own
middleware, so a 500 would otherwise skip the headers.

### The confining layers, and what each alone stops

| Layer | What it alone stops |
| --- | --- |
| Root `vercel.json`: one extra rewrite, `/partner/v1/(.*)` to the backend, before the catch-all | A request to the public domain reaching any backend path other than `/webhooks/stripe` and the partner prefix. `/partner/v1` itself, `/partner/v2/...`, `/partnerx/...`, `/v1/...`, `/docs` and `/openapi.json` go to the frontend. |
| One mount, first in the route table, matching by string; no main-app route under `/partner` or able to match it | A request under the prefix being dispatched to an internal handler. The app never normalises `..`, `%2e%2e`, `%0A` or `//`; they stay inside the prefix and meet the sub-app's 404. |
| `require_internal_key` on every main-app route (or an explicit allowlist) | A caller who reaches a main-app route by some other path, because a rewrite was widened or a hostname falls through, and has no internal key. |
| A distinct credential, checked before routing and at the sub-app (FREEJ-57) | A partner key doing anything on an internal route, and a stolen internal key doing anything as a partner. |
| The surface wrapper | Cookies, CORS, caching, docs and oversized bodies on the partner surface; a switched-off surface answering anything but 404. |
| Scoping every partner query to the key's merchant (planned) | One merchant reading or changing another's data. This is the second half of the invariant. |
| A restricted database role through a separate session provider and `PARTNER_DATABASE_URL` (FREEJ-57) | A bug in a partner handler reading or writing tables a partner never needs (users, sessions, billing). |

### The proofs that stay in the gate

All run in the normal `pytest` run, so `scripts/gate.sh` and CI carry them:

- `tests/core/test_vercel_routing.py` parses the real `vercel.json`. The backend sources are exactly the Stripe
  path and the partner prefix. Listed paths reach the backend or the frontend as described above. Every rewrite
  has exactly the keys `source` and `destination` and one of the two known destinations; the file's top-level keys
  and the keys of the backend service are exactly the current ones; the backend service's own `vercel.json` has
  no routing key. In CI a missing root `vercel.json` fails instead of skipping.
- `tests/api/test_route_confinement.py` (walker in `tests/confinement_walker.py`) walks every route of the
  application, mounts and low-priority routes included: exactly one mount at `/partner/v1`, a `PartnerMount`
  around a `PartnerSurface`, first in the table; every main-app route has `require_internal_key` in its dependency
  tree or is on the allowlist (`/health`, `/webhooks/stripe`, and the docs routes only while enabled); no main-app
  route starts with `/partner` in any case, and no main-app route's own path pattern can match `/partner/v1`,
  `/partner/v1/`, `/partner/v1/x` or `/partner/v1/x/y` (so `/{a}/v1/{b}` and `/{rest:path}` fail), nor
  `/webhooks/stripe` unless it is the Stripe route; no main-app route except Stripe is matched by a backend
  rewrite; no dependency overrides; every route inside the mount has an endpoint defined in `app.partner`, shared
  with no main-app route, without the internal key. A behavioural test calls every non-allowlisted main-app route
  with no key and expects 401. Throwaway apps prove the walker fails on each rule.
- `tests/api/partner/test_partner_surface.py`, `test_partner_mount.py` and `test_partner_body_cap.py` send
  adversarial paths (newline, dot segments, encoded slashes, `//`, a `root_path` of the prefix) as raw ASGI scopes
  (an HTTP client would normalise them away) and check the same 404, the exact header set on every response, no
  CORS on `OPTIONS`, the switch, the body cap (including application code that swallows the overflow, and
  middleware or streaming responses that read or ignore the body) and one error log per failing request. The
  surface test also pins the main app's middleware list (see the residual risks).
- `tests/api/partner/test_partner_isolation.py` reads the source of every module in `app/partner/` and fails on any
  import from `app` other than `app.core` and `app.partner`, on dynamic imports, on a relative import that
  climbs out of the package, and on any use of the internal credentials, the client-IP helpers or the owner session; and checks the
  mounted sub-app has the same routes the schema is generated from. Because `app/partner` may not import models, the key format, the
  rate-limit enforcement helpers and the limiter singleton live in `app/core`, and the key lookup uses SQLAlchemy Core table definitions
  in `app/partner/keys.py` that `test_partner_table_definitions.py` checks against the ORM metadata.
- FREEJ-57 added `test_partner_auth_attached.py`, `test_partner_credentials.py`, `test_partner_auth.py`, `test_partner_limits.py`,
  `test_partner_errors.py`, `tests/api/test_partner_key_rejected_elsewhere.py` (a valid partner key opens nothing on any main-app route),
  the key lifecycle e2e tests and `tests/core/test_partner_role_e2e.py` (the restricted role, on Postgres). See
  [backend-partner-api.md](../developer/backend-partner-api.md).
- `tests/commands/test_partner_openapi_schema.py` and `commands/check_partner_openapi_schema.py` (pre-commit and
  `scripts/gate.sh`): every path of the partner schema starts with `/partner/v1/`; the internal schema has no
  `/partner` path; the committed partner schema is current.

### Accepted residual risk

**Partner code shares the backend process and its secrets.** The Firebase Admin key, the Stripe keys, the
internal key and the full-privilege `DATABASE_URL` are all in the environment of the process that serves
`/partner/v1`. A flaw in partner code that gives a caller code execution, a request to an internal address or a
file read could expose them, whatever the layers above say. The restricted database role limits what the partner
*session provider* can do; it does not remove the other secrets from the process. This was accepted because a
separate service was decided against. It is managed by keeping the partner surface small, validating every input
at the boundary, and the triggers below.

**Unauthenticated traffic reaches the backend function even while the surface is off.** The rewrite sends every
`/partner/v1/...` request to the backend, which answers 404 itself. That costs function time and concurrency shared
with the internal API, and nothing rate-limits it with the switch (the partner rate-limit policies, keyed by key id, came with FREEJ-57; the failed-authentication limit does NOT bound database work: it only turns repeated failures for one key id into a 429. Unauthenticated lookups are bounded only by the edge rule and the role's connection limit). The first
mitigation is a Vercel Firewall rate rule on `/partner/v1/` (a launch precondition below).

**The body is buffered by Vercel before the cap runs.** The surface does not buffer it, but the platform may hold the
whole request before the ASGI app sees it, so "never buffered" is true of the surface only, and the 256 KiB cap is
a defence for the function, not for the platform's own limits.

**Middleware of the main app wraps the surface from outside.** Anything added to the main app (a CORS or session
middleware, a response hook) also runs on partner responses and the surface cannot undo it. So the list of main-app
middleware is pinned by a test; adding to it has to be a decision that also deals with `/partner/v1`.

### Launch preconditions (owner steps; not done by this story)

1. **Remove `API_DOCS_ENABLED` from the production backend** (the go-live step of ADR-003) and redeploy. While it
   is set, `/docs`, `/redoc` and `/openapi.json` serve the whole internal schema with no key.
2. **Turn on Deployment Protection for preview deployments.** A preview that has `PARTNER_API_ENABLED=true` must
   not be reachable by the public.
   **Add a Vercel Firewall rate rule on `/partner/v1/`**, so unauthenticated traffic to the surface is limited
   at the edge, whether the surface is on or off.
3. **Run the post-deploy smoke against the real domain** (`scripts/smoke-partner-surface.sh`, the
   `partner-surface` job in `.github/workflows/post-deploy-smoke.yml`). It needs no secret and passes whether the
   surface is off (404) or on (a "must" path answers 404 or 401, with the surface header, because authentication comes before
   routing). It fails if the backend answers a request under or around the prefix without the
   `X-Freejoy-Surface` header. Read the first production run, because it is the first time these paths meet
   Vercel's edge (below).
4. Create the `freejoy_partner` role from `commands/sql/partner_role.sql` and set `PARTNER_DATABASE_URL` (ending `?sslmode=verify-full`).
   Startup refuses `PARTNER_API_ENABLED=true` without it, and refuses a URL whose user is the owner's. **The Vercel Firewall rate rule in
   step 2 is a condition of `PARTNER_API_ENABLED=true`**, not a nicety: nothing else bounds unauthenticated lookups.
5. Do not set `PARTNER_API_ENABLED=true` in production until the scoping story (FREEJ-58) and the submissions story (FREEJ-59) are in and reviewed.

### Amendments to earlier records

- **ADR-003.** Partners are the second machine caller that its "Alternatives considered" section anticipated
  ("revisit only if a second machine caller appears"). They do not use `X-Internal-Key` or the forwarded session:
  the partner surface has its own credential, described under *The mechanism* and *Keys and the restricted role*. Section 5 of ADR-003 said the
  backend URL is public. After ADR-011 it is not: only `/webhooks/stripe` and now `/partner/v1/...` are rewritten
  to it.
- **ADR-004.** The layering gains `app/partner/`, a package that imports only `app.core` and itself (checked by a
  test). The lint rule ADR-004 described (`TID251`) was not configured when this record was written; FREEJ-56
  configured it, and ADR-004's text now says exactly what it bans.
- **ADR-007.** Partner endpoints get their own rate-limit policies, keyed by key id, never by user, organisation or client address
  (partner code does not read forwarded client-IP headers): `PARTNER_READ_POLICY` (fails open), `PARTNER_WRITE_POLICY` (fails closed) and
  `PARTNER_AUTH_FAILURE_POLICY` (failed authentications of a well-formed key, per presented key id; fails closed; a request with no header, an unparseable key or mixed credentials is never counted). The read and write limits are applied by the surface right after authentication, before routing, so they cover unmatched paths too. The
  enforcement helpers and the limiter singleton moved to `app/core` so the partner package can use them.
- **ADR-008.** The partner contract is a separate, versioned document (`docs/partner/openapi.json`,
  prefix `/partner/v1`) and is not part of `nextjs-frontend/openapi.json` or the generated client. The envelope is
  the same. A breaking change to it is a new prefix (`/partner/v2`), not an edit.
- **ADR-011.** The backend now has a second public rewrite. Like the Stripe one it sits before the catch-all and
  names an exact prefix, and the service receives the original path, so the sub-app is mounted under the same
  prefix.

### Keys and the restricted role (FREEJ-57), and the design still to come

- **Key format (built):** `Authorization: Bearer fj_<env>_<key_id>_<secret>`; `key_id` and `secret` from fixed alphabets without `_` and
  fixed lengths, one anchored ASCII pattern, an `<env>` that is not the running environment is refused. The secret is 32 bytes from
  `secrets`. The SHA-256 of the **whole key** is stored (the secret is 256 bits from a CSPRNG, so nothing needs a slow hash or a salt), the key id
  selects the row, the comparison is constant time, and an unknown id costs the same comparison against a dummy.
- **Restricted database role (built):** `freejoy_partner`, created from `fastapi_backend/commands/sql/partner_role.sql` by the owner, reached through
  `PartnerSessionProvider` and `PARTNER_DATABASE_URL` (ADR-004's session seam; required when the switch is on). `SELECT` and `UPDATE (last_used_at)` on
  `partner_api_keys` (`last_used_at` only) and `SELECT` on the columns the code reads of `merchants`, as column grants; no access to `audit_logs`, nothing else, no DDL in `public`. The script verifies this from the catalog and raises otherwise. The connection limit and the Firewall rule are the only bound on unauthenticated lookups.
- **Tables:** `partner_api_keys` (built, migration `0007`) and `partner_submissions` (built, migration `0008`, FREEJ-58).
- **Submissions create drafts only** (built, FREEJ-58). A partner can never publish; the existing review and publish rules stay
  ours. How it is enforced, in layers: the API only ever inserts a draft; the role has no `INSERT` or `UPDATE` on `offers.status`,
  `last_verified_at`, `featured` or `commercial_basis` (the column default supplies `DRAFT`); the role may write `affiliate_links` only as
  a `direct` link (a trigger supplies the provider) whose host the service checks against the merchant's stored website, which the role can
  never write; an update of anything but an untouched draft is stored as a pending proposal for an editor, applied only through the admin's
  normal edit path with the review token. The pure rules both sides need moved to `app/core` and are re-exported by their old modules, so
  `app/partner` still imports only `app.core` and itself.
- **Audit for the partner role** (FREEJ-58). `audit_logs` is not granted bare. The role gets an `INSERT` of five columns (not `user_id`,
  not `organization_id`, no read, no update, no delete) and the trigger `freejoy_audit_insert_guard` lets a writer that may not name a user
  append only `partner.<area>.<verb>` rows, so it cannot forge an `admin.` row. Chosen over a separate `partner_audit_logs` table because
  the admin audit view then shows partner activity unchanged. Actions: `partner.offers.submit`, `partner.offers.update`,
  `partner.offers.end`; the key id is the actor.
- **`commercial_basis`** is NOT NULL with no default, and the role may not write it. A trigger gives an insert by a writer that may not set
  it the placeholder `EDITORIAL`; the editor sets the real value at review, and a `direct`-only offer still needs the editor's explicit
  confirmation of the basis to publish. If the owner prefers a different placeholder, it is one line of migration 0008.

## Alternatives considered

- **A separate partner project or service.** Gives process isolation: partner code would not share secrets
  with the internal API. Decided against by the owner for cost and simplicity; see the triggers below.
- **Partner routes added to the main router with a per-route key check.** One forgotten dependency exposes an
  internal route; a mount confines by construction and can be checked by one walk of the route table.
- **Reusing `X-Internal-Key` for partners.** One leaked partner key would be the service key of the whole backend.
- **`/partner/v1` served with docs enabled for retailers.** A public schema of the partner surface helps an
  attacker more than a retailer; the contract is shared as a file.

## Open question: the hostname

Whether partners should call `partners.<domain>` instead of a path on the main domain is **open**. A
dedicated host would let Vercel's Deployment Protection, firewall rules and rate limits be set for the partner
surface alone, and keep the retailer-facing URL stable if the surface later moves.

What is not known (unverified; it cannot be checked offline):

- Whether a rewrite whose destination is a service accepts `has` conditions of type `host`. Vercel documents `has`
  for rewrites generally, not for service destinations.
- Whether a service gets a directly reachable URL of its own when no rewrite points at it, which decides how
  exposed the backend's non-partner routes are on a second hostname. The internal key still covers them.
- Whether the edge normalises `..`, `%2e%2e` or `//` before matching a rewrite. The routing test assumes it does
  not and matches the documented behaviour (case-sensitive, first match wins, original path kept). It also assumes
  a trailing slash matters and that `(.*)` matches an empty remainder; these are assumptions, not facts.

If a hostname is chosen, the rewrite needs the same exact-prefix rule and `tests/vercel_rewrites.py` must learn
`has` (today it refuses any conditional rewrite, so the test fails instead of mis-matching).

## When to revisit process isolation

Move the partner API into its own service (or project) when any of these happens:

- a retailer's security terms require that partner traffic does not share a process or secrets with the
  internal API;
- partner traffic contends with the internal API for capacity, or needs a different scaling or timeout profile;
- a second team owns the partner code.

## Consequences

- The partner surface exists, authenticates before routing and serves one endpoint (`GET /partner/v1/me`); every proof is in the gate.
  Adding the next endpoint (FREEJ-58) changes one file set and must keep the proofs green.
- Every backend change that adds a route now passes through the confinement walk: an open route needs an explicit
  allowlist entry with a reason.
- `scripts/gate.sh` and pre-commit gain a second schema check, for `docs/partner/openapi.json`.
- The residual risk above remains until process isolation is chosen.
