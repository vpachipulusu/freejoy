# ADR-008: Typed API client generated from OpenAPI

- **Status:** Accepted by the product owner, 2026-10-12
- **Date:** 2026-10-11
- **Amended by:** [ADR-013](0013-partner-api.md) (the partner contract is a separate versioned document)

## Context

The frontend and backend are written in different languages and deploy separately. Hand-written fetch calls
and types would drift from the API. The template already writes FastAPI's OpenAPI schema into the frontend.
The frontend's components and server actions currently expect a `{ success, data?, error? }` response shape.

## Decision

- **Contract:** FastAPI's OpenAPI schema is the contract. `commands/generate_openapi_schema` writes
  `nextjs-frontend/openapi.json`, and `watcher.py` regenerates it during development. `@hey-api/openapi-ts`
  generates a typed fetch client into `nextjs-frontend/src/lib/api/`.
- **Server-only:** the generated client is used only through a wrapper marked `import 'server-only'`, which
  adds the service key and the forwarded session ([ADR-003](0003-nextjs-to-fastapi-authentication.md)), so it
  cannot end up in a browser bundle.
- **Staleness check:** CI and a pre-commit hook regenerate the schema and client and fail if the result differs
  from what is committed.
- **Envelope:** every endpoint returns `{ success: true, data }` or `{ success: false, error }`, declared
  in OpenAPI as a generic `Envelope[T]` so the generated types stay exact. HTTP status codes still carry
  meaning (4xx/5xx with `success: false`).

## Alternatives considered

- **Hand-written types and fetch calls.** Drift is found at runtime instead of at build time.
- **tRPC or GraphQL.** Would mean a TypeScript backend or a second schema layer; FastAPI already produces
  OpenAPI.
- **Plain JSON bodies without an envelope.** More conventional for REST, but every existing frontend call site
  would need rewriting. The envelope can be dropped later in a versioned API.

## Consequences

- Backend changes that break the frontend fail typecheck in the same PR.
- Generated files are committed and show up in diffs; reviewers skip them, and the staleness check guards them.
- The envelope means slightly more wrapping in FastAPI responses and handlers, centralised in `core/`
  ([ADR-004](0004-backend-layering.md)).
