# Architecture decision records

One record per decision. The code and the developer docs describe *what* exists; these records hold *why*.
Write a new ADR when you change a decision below, instead of editing the old one: mark the old record
"Superseded by ADR-NNN" and keep it for history.

## Index

| ADR | Title | Status |
|---|---|---|
| [001](0001-adopt-nextjs-fastapi-template.md) | Adopt the nextjs-fastapi-template backend | Accepted |
| [003](0003-nextjs-to-fastapi-authentication.md) | Authentication from the Next.js server to FastAPI | Accepted |
| [004](0004-backend-layering.md) | Backend layering (routers, services, repositories) | Accepted |
| [006](0006-docker-role.md) | Docker for local development and CI only | Accepted |
| [007](0007-rate-limit-keys.md) | Rate limits keyed by user or organisation | Accepted |
| [008](0008-api-client-generation.md) | Typed API client generated from OpenAPI | Accepted |
| [011](0011-single-vercel-project-with-services.md) | One Vercel project with services | Accepted |
| [012](0012-email-provider.md) | Email provider for alerts (Brevo) | Proposed |
| [013](0013-partner-api.md) | Partner API inside the backend | Proposed |

Numbers 002, 005, 009 and 010 belonged to the product this template was extracted from and are not part of
the template. Records 012 and 013 are Freejoy's own; start new ones at 014.

## Statuses

- **Proposed:** written, awaiting sign-off.
- **Accepted:** signed off; change only by a new ADR that supersedes it.
- **Provisional:** accepted as the working decision but due for review at a named checkpoint.
- **Superseded by ADR-NNN:** replaced; kept for history.

## Template

```markdown
# ADR-NNN: Title

- **Status:** Proposed
- **Date:** YYYY-MM-DD
- **Deciders:** who signed off

## Context

What problem or force makes a decision necessary. Facts, constraints, numbers.

## Decision

What was decided, in one or two paragraphs.

## Alternatives considered

Each alternative with the reason it was not chosen.

## Consequences

What becomes easier, what becomes harder, what must be revisited and when.
```
