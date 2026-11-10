# ADR-004: Backend layering

- **Status:** Accepted by the product owner, 2026-10-12
- **Date:** 2026-10-11

## Context

The template keeps routes, models and the database in a few flat modules (`main.py`, `models.py`,
`database.py`, `users.py`). A product built on it will have many domains (auth, organisations, billing and whatever the product adds),
so the place where the database is opened has to be easy to find and control.

## Decision

`fastapi_backend/app/` is organised by layer, and dependencies only point downwards:

```
api/v1/        HTTP only: routers, request/response schemas, dependencies (auth, membership)
services/      business logic and transaction boundaries; no FastAPI imports
repositories/ SQLAlchemy queries for one aggregate each; no business rules
models/platform/ SQLAlchemy models, one Alembic tree
core/          config (pydantic-settings), database seam, structured logging, error types and handlers, Sentry
partner/       the partner sub-app and its surface (ADR-013); imports `core` only
```

Rules:
- Routers call services; services call repositories; repositories receive an `AsyncSession` and never create
  one.
- Engines are built only in `app/core/database.py`. A lint rule (ruff `TID251`, configured in
  `fastapi_backend/pyproject.toml`) bans `create_engine`, `create_async_engine` and `build_engine` everywhere else.
  Session makers are not banned: commands and tests receive one from that module.
- Services raise domain errors (`core/errors`). One exception handler turns them into the
  `{ success, data?, error? }` envelope ([ADR-008](0008-api-client-generation.md)) with the right HTTP status,
  with no stack traces or internal details.
- Logging is structured (JSON) through one logger set up in `core/`; no `print()`.
- Configuration comes only from `core/config`; required secrets are checked at startup.

## Where engines are built

Only `app/core/database.py` builds engines (`build_engine`, the platform engine and the session maker behind
`SessionProvider`). Ruff `TID251` (`[tool.ruff.lint.flake8-tidy-imports.banned-api]`
in `fastapi_backend/pyproject.toml`, added by FREEJ-56: the rule was written here before it was configured) bans `create_engine`, `create_async_engine` and `build_engine` everywhere
except that module, `alembic_migrations/env.py` and tests, so CI fails on an engine built anywhere else. Every
engine, and Alembic, takes its connect args from `app/core/db_url.py` (`to_async_target`): the TLS mode from the
URL, asyncpg's statement cache off (safe behind a connection pooler) and a bounded connect timeout.

## Alternatives considered

- **Keep the template's flat layout.** Fine for an example app; at Your App's size it would mix HTTP,
  business rules and tenant routing in the same modules.
- **Feature folders (one package per domain, each with its own layers).** Good later. Starting with layers
  keeps the database seam in one place while there are only a few domains; domain sub-packages can be added
  inside each layer as they grow.
- **Hexagonal architecture with ports for every dependency.** More ceremony than a small team needs. The one
  seam that must be swappable (the database session) gets an explicit interface (`SessionProvider`).

## Consequences

- Services and repositories can be unit-tested with a session fixture and no HTTP.
- New code has an obvious home, and review can check "no session created outside `db/`" mechanically.
- Some thin pass-through code (router → service → repository) for simple reads; accepted for consistency.
