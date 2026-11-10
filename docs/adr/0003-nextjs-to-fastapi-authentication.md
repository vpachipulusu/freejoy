# ADR-003: Authentication from the Next.js server to FastAPI

- **Status:** Accepted by the product owner, 2026-10-12
- **Date:** 2026-10-11

## Context

Users sign in with Firebase. The Next.js server verifies the ID token and sets an httpOnly
`session` cookie holding a Firebase session cookie. The backend is reachable only through the Next.js server (ADR-011), but any request reaching it must still
prove two things: that it came from our Next.js server, and which user it acts for.

## Decision

1. **Only the Next.js server calls FastAPI.** Server components, server actions and route handlers call it
   through the generated client ([ADR-008](0008-api-client-generation.md)); the browser never does. The
   backend therefore allows no browser origins in CORS.
2. **Firebase stays the identity provider.** The template's fastapi-users and JWT secrets are
   removed.
3. **Each backend request carries two credentials:**
   - **Internal service key:** a shared secret in an `X-Internal-Key` header, held only in the server
     environments of both Vercel projects (the Next.js server reads it from `BACKEND_INTERNAL_KEY`, never a
     `NEXT_PUBLIC_*` variable). FastAPI compares it in constant time and rejects requests without
     it before any other work. It proves the caller is our Next.js server. Two valid keys (current and next)
     are accepted so the key can be rotated without downtime. **Each environment has its own keys**
     (production, preview, development), so a preview frontend cannot call the production backend.
   - **Forwarded Firebase session cookie:** the value of the `session` cookie, forwarded in an
     `X-Session-Token` header (not as a browser cookie). FastAPI verifies it with the Firebase Admin SDK,
     including the revocation check, and turns it into the current user. This proves *who* the request is
     for. The revocation check costs a Firebase round trip per request.
   - Both headers are redacted from request logs and Sentry events.
4. **Signing in:** Next.js posts the Firebase ID token to `POST /v1/auth/session`. FastAPI verifies it, rejects
   tokens whose `auth_time` is older than 5 minutes (Firebase's guidance for minting session cookies), syncs the
   user, records login history and returns the session cookie value. Next.js sets the cookie in the browser.
   The Firebase Admin credentials then live only in the backend.
5. **Endpoints not called by Next.js** (provider webhooks such as Stripe) do not use the service key; each
   verifies its provider's signature instead. `/health` (liveness, no database) is also public and returns no internal detail; `/health/ready` queries the
   database, so it requires the service key. Because
   the backend URL is public, the OpenAPI document and docs UI are switched off in production.
6. **The client IP** for unauthenticated endpoints is forwarded by Next.js in an `X-Client-IP` header, and is
   trusted only on requests that carry a valid service key ([ADR-007](0007-rate-limit-keys.md)). Next.js takes
   it from the header set by the platform (Vercel's `x-vercel-forwarded-for`, or `x-real-ip`), never from the first client-supplied `X-Forwarded-For`
   entry. FastAPI ignores inbound `X-Forwarded-For`.
7. **Authorisation** happens in the backend, in dependencies that run before any data is read:
   organisation membership and role, and the platform-admin role for admin endpoints. Email verification is enforced here from the verified token
   claims (`email_verified`), not from a cookie the browser can set.

## Testing-phase exception: API docs in production

Production is still in testing, so the owner decided on 2026-11-09 to serve Swagger on the production backend
for now. This overrides the default above (no schema or docs UI in production) for the testing phase only.

- The opt-in setting `API_DOCS_ENABLED` (default `false`) serves `/docs`, `/redoc` and the OpenAPI schema in a
  non-relaxed environment. It relaxes no other production check. The backend logs one warning at startup while it
  is on in such an environment.
- The docs UI has no Authorize button: the `X-Internal-Key` header is not in the schema, so "Try it out" on
  protected routes answers 401. The schema is the same document as `nextjs-frontend/openapi.json`.
- **Go-live step:** delete `API_DOCS_ENABLED` from the Vercel backend project's environment variables and redeploy
  the backend. Production then returns 404 for `/docs`, `/redoc` and `/openapi.json` again.

## Alternatives considered

- **Session cookie only, no service key.** Anyone holding a stolen session cookie could call the backend
  directly, skipping the Next.js layer's checks and rate limits.
- **Next.js mints short-lived JWTs for the backend.** Adds signing keys and a second token format; Firebase
  already gives a verifiable, revocable session.
- **Vercel Deployment Protection / private networking.** Not available for function-to-function calls on all
  plans and ties security to one host. Can be added on top later.
- **OAuth 2.0 (client credentials or an authorization server) between Next.js and FastAPI.** Rejected:
  there is one caller, the Next.js server, so a shared rotatable service key plus the revocable Firebase session
  gives the same assurance without a token issuer, scopes or a second token lifecycle. Revisit only if a second
  machine caller appears.
- **Browser calls FastAPI directly with an ID token.** Exposes the backend to the internet as a public API
  and puts CORS and token refresh in the browser, which goes against decision 1.

## Consequences

- A leaked session cookie alone cannot reach the backend; an attacker would also need a server secret.
- The service key is a high-value secret: it lives only in server env vars (never `NEXT_PUBLIC_*`), it is
  rotated by the two-key scheme, and it is never logged.
- The Firebase Admin SDK, and its private key, live in the backend, never in the frontend.
- Every backend call from Next.js goes through one server-only wrapper that attaches both headers.
