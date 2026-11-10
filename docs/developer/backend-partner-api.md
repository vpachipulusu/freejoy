# Backend: the partner surface

The partner API is how retailers will submit offers to Freejoy. This page covers what exists today
(FREEJ-56): an **empty, switched-off surface** and the tests that keep it confined. There is no endpoint, key,
authentication, database role or table yet; those are FREEJ-57 to FREEJ-59. The reasons are in
[ADR-013](../adr/0013-partner-api.md).

## What exists

| Piece | Where |
| --- | --- |
| The sub-app (no docs, no schema route, no slash redirects, the shared error envelope) | `app/partner/app.py` (`create_partner_api`) |
| The wrapper that mounts it: switch, body cap, headers | `app/partner/surface.py` (`PartnerSurface`) |
| The mount, which also takes the bare prefix `/partner/v1` | `app/partner/mount.py` (`PartnerMount`), added in `app/main.py` |
| The switch | `PARTNER_API_ENABLED` in `app/core/config.py` (default `false`; `.env.example`) |
| The partner OpenAPI document | `docs/partner/openapi.json`, written by `commands/generate_partner_openapi_schema.py` |
| Its freshness check | `commands/check_partner_openapi_schema.py` (pre-commit and `scripts/gate.sh`) |
| The public route | the rewrite `/partner/v1/(.*)` in the root `vercel.json`, before the catch-all |
| Post-deploy smoke | `scripts/smoke-partner-surface.sh`, the `partner-surface` job of `.github/workflows/post-deploy-smoke.yml` |

`app/partner/` imports only `app.core`, never `app.api` ([ADR-004](../adr/0004-backend-layering.md)).

## What a caller sees

Every response under `/partner/v1`, errors and 404s included:

- `Cache-Control: no-store, private`, `X-Content-Type-Options: nosniff`, `X-Freejoy-Surface: partner`;
- never `Set-Cookie`, and no `Access-Control-*` header, including on `OPTIONS` (a retailer calls from a server);
- the usual envelope, `{"success": false, "error": "<fixed text>"}`, with nothing from the request in it.

While `PARTNER_API_ENABLED` is `false` (the default) every path under the prefix answers the same 404 as an
unknown path does while it is on: same body, same headers, no `Location`. That includes `/partner/v1`,
`/partner/v1/`, `/partner/v1/docs`, `/partner/v1/openapi.json`, `/partner/v1/../v1/me`, `/partner/v1/%2e%2e/v1/me`
and `/partner/v1//x`. The app never normalises a path, so those stay inside the prefix.

A request body over 256 KiB (`MAX_BODY_BYTES`) gets a 413. The cap is checked on the declared `Content-Length`
and again on the bytes actually received, so a missing or false length does not get round it and the body is
never buffered whole. Raise the constant deliberately when a real endpoint needs more.

The headers, the switch and the cap are applied by `PartnerSurface`, a wrapper around the sub-app, not middleware
inside it: a FastAPI app's error middleware sits outside its own middleware, so a 500 would otherwise miss them.

## How confinement works

The invariant ([ADR-013](../adr/0013-partner-api.md)): a caller holding a valid partner key and the public
partner URL can invoke only partner handlers, and can read or change only its own merchant's data.

1. **Vercel** sends only `/webhooks/stripe` and `/partner/v1/(.*)` to the backend. Everything else, including
   `/partner/v1` without a slash, goes to the frontend.
2. **One mount.** Partner handlers exist only inside the mount at `/partner/v1`. No main-app route starts with
   `/partner`, in any letter case.
3. **The internal key.** Every main-app route needs `X-Internal-Key` ([ADR-003](../adr/0003-nextjs-to-fastapi-authentication.md)),
   apart from `/health` and `/webhooks/stripe`, which have their own rules, and the docs routes where they are enabled.
4. **A distinct partner credential** (planned, FREEJ-57/58) and **scoping to the key's merchant** and a
   **restricted database role** (planned). None of these is built yet.

Layers 1 to 3 are proved by tests that run in the normal suite. Layer 4 is the work of the next stories.

One thing the surface cannot undo: middleware added to the **main** app runs outside it and could add CORS headers
or cookies to partner responses. The main app has only the request-ID middleware, and a test pins that list. If
you need to add middleware to `app/main.py`, that test will fail; decide there how `/partner/v1` is treated.

## Adding the first endpoint (for FREEJ-58)

1. Add the router to the sub-app in `app/partner/app.py`. Paths are written relative to the mount (`/offers`, not
   `/partner/v1/offers`).
2. Attach authentication to the sub-app, not to each route.
3. Regenerate the partner schema (below). The generator writes the full path, `/partner/v1/offers`.
4. Run the proofs. `tests/api/test_route_confinement.py` and `tests/api/partner/test_partner_surface.py` must stay
   green; extend the latter with the new endpoint's behaviour, and put the endpoint's own tests beside it.

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
| `tests/api/test_route_confinement.py` | One mount; every main-app route needs the internal key or is allowlisted; nothing main-app under `/partner`; only Stripe is reachable by a backend rewrite. Negative tests show the walker fails on bad apps |
| `tests/api/partner/test_partner_surface.py` | The same 404 for adversarial paths, the headers on every response, no CORS on `OPTIONS`, the body cap, the switch |
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
3. Check the first production run of the `partner-surface` smoke job. It needs no secret and passes while the
   surface is off. If it fails, the backend answered a path under or around the prefix without the surface header:
   read its log before changing anything. It is also a manual step: `bash scripts/smoke-partner-surface.sh https://<domain>`.
4. Leave `PARTNER_API_ENABLED` unset in production until FREEJ-57 to 59 are in.

The smoke is a workflow job rather than a manual step because it can run without secrets and cannot break while the
surface is off. Whether the real Vercel edge treats the `..`, `%2e%2e` and `//` paths the way the script assumes has
not been seen yet; the script only insists on the surface header for paths that cannot leave the prefix.

## Not built yet

Keys (`Authorization: Bearer fj_<env>_<key_id>_<secret>`, stored as a SHA-256), authentication, rate-limit policies,
the restricted database role (`PARTNER_DATABASE_URL`), the tables `partner_api_keys` and `partner_submissions`, and
every endpoint. Submissions will create drafts only. The hostname (`partners.<domain>` or the path on the main
domain) is an open question, set out in ADR-013.
