# ADR-007: Rate limits keyed by user or organisation

- **Status:** Accepted by the product owner, 2026-10-12
- **Date:** 2026-10-11
- **Amended by:** [ADR-013](0013-partner-api.md) (partner endpoints get their own policies, keyed by key id)

## Context

The boilerplate rate-limits by client IP in the Next.js proxy. Once the backend exists, every request it
receives comes from Vercel's servers ([ADR-003](0003-nextjs-to-fastapi-authentication.md)), so the IP it sees
says nothing about the end user. In-process counters do not work on serverless functions either. On top of
that, IP keys treat many shoppers behind one network as one client, and let one attacker spread requests across
many IPs.

## Decision

- **Authenticated requests are limited by who is acting:** the user, and where relevant also by
  organisation. Keys look like `rl:{scope}:{user|org}:{id}`.
- **Unauthenticated auth endpoints** (sign-in, sign-up, password reset) are limited by the end-user IP that
  Next.js forwards, trusted only on requests that carry a valid service key, combined with the target account
  (for example email) where one exists.
- **Counters live in Upstash Redis** (HTTP), never in process memory.
- Going over a limit returns **429** with standard rate-limit headers, in the normal error envelope.
- The starting limits match the boilerplate's current auth, API and webhook settings. **What happens when Redis
  is unavailable** is decided per policy and tested.
- The backend limits are the ones that count. The current in-memory limiter used by the Next.js proxy
  (`src/lib/middleware/rate-limit.ts`, a per-instance `Map`) gives no real protection on serverless. Any
  limit kept in the proxy must also use Upstash, or be removed.

## Alternatives considered

- **IP limits in the backend.** Meaningless behind the Next.js server (every request has a Vercel IP).
- **In-memory limits per function instance.** Reset on every cold start and differ per instance; no real
  protection.
- **Limits only in the Next.js proxy.** The backend has a public URL, so it must protect itself.

## Consequences

- Fair limits per user and per organisation, which can later follow the organisation's plan.
- Every limited request makes a Redis round trip; Upstash's HTTP latency is part of the request budget.
- Trusting the forwarded IP depends on the service key staying secret.

## Implementation notes

- Keys: `rl:{scope}:{user|org}:{id}` for authenticated routes; sign-in, sign-up and password reset use
  `rl:auth:ip:{X-Client-IP}` and `rl:auth:email:{sha256(lowercased email)}`. Limits: auth 5 per 15 minutes,
  API 100 per minute, webhook 200 per minute (sliding window).
- Timeouts and retry. Fail-open policies keep the 200 ms Redis timeout and are never retried.
  Fail-closed policies (sign-in, data export, admin sync) get a 1 s deadline per identifier, because a cold
  connection to a distant region can exceed 200 ms and a fail-closed timeout locks users out. A fail-closed call
  is retried once, only on a connect error (`ConnectTimeout` is rarely reachable: the Upstash client sets no
  httpx timeout, so the 1 s budget fires first), and only when 200 ms of the deadline is left. The deadline is
  per identifier and identifiers are hit one after another, so a slow Redis can hold a worker for up to about
  2 s on sign-in (IP then email) before the 503.
- Failure modes. A client that fails at the transport level is replaced, at most once per second
  however many calls fail, and the old client is closed after a grace period. A plain timeout means slow, not
  broken, so it is neither retried nor does it discard the client. The client is also replaced when the event
  loop changes. Each failed call logs a warning with the policy and exception class only; the outage handler
  also logs the traceback (with `exc_info`) when it rejects or lets a request through, which carries the
  httpx error text (no token or URL: the token travels in a header). **Auth routes fail closed** (503 with `Retry-After`); **authenticated API and webhook
  routes fail open** (warning log with request ID, and a Sentry event once Sentry is configured).
- Until the internal service key exists, `X-Client-IP` is trusted only because the backend is
  internal (ADR-003); it is not yet gated on `X-Internal-Key`. If the Upstash settings are unset, limiting is
  disabled only when `ENVIRONMENT` is `local` or `test`, or on Vercel when `VERCEL_ENV` (set by the platform, and
  preferred over `ENVIRONMENT`) is `preview` or `development`. **Production needs `UPSTASH_REDIS_REST_URL` and
  `UPSTASH_REDIS_REST_TOKEN` set before the next release.** `ENVIRONMENT` defaults to `production`, so any other
  environment without both Upstash settings fails at startup. `/health/ready` does not check Redis: limiting fails
  open, so a Redis blip must not pull the service out of rotation.
