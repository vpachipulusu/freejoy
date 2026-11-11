"""Partner rate limits (ADR-007, ADR-013), applied where authentication happens: before routing.

After a key authenticates, ``PartnerAuthenticator`` calls ``enforce_key_limit`` once for the request, so the limit
covers every authenticated request whether or not it matches a route (an unknown path or a wrong method costs a
database connection like any other) and a matched route is never counted twice. The policy comes from the method: a
read (``GET``, ``HEAD``, ``OPTIONS``) is ``PARTNER_READ_POLICY`` (fails open), anything else is
``PARTNER_WRITE_POLICY`` (fails closed). Both are keyed by the key id (``rl:partner_read:key:<key id>``), never by
client address. Failed authentications are a separate policy, ``PARTNER_AUTH_FAILURE_POLICY``, in ``auth.py``.

Only a 429 carries ``X-RateLimit-*`` and ``Retry-After``: a successful response does not (the surface answers the
429 and the 503 itself, before the sub-app runs).
"""

import logging

from starlette.responses import Response

from app.core.errors import error_response
from app.core.rate_limit import (
    PARTNER_READ_POLICY,
    PARTNER_WRITE_POLICY,
    RateLimiter,
    RateLimitPolicy,
)
from app.core.rate_limit_enforcement import (
    FAIL_CLOSED_RETRY_AFTER_SECONDS,
    RATE_LIMIT_FAIL_CLOSED_RESPONSES,
    TOO_MANY_REQUESTS_MESSAGE,
    UNAVAILABLE_MESSAGE,
    rate_limit_headers,
    retry_after_seconds,
)
from app.core.sentry import report_exception

logger = logging.getLogger("yourapp.partner")

READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# Declared on the sub-app so the partner contract lists them: a 429 for any call, a 503 where the database or a
# fail-closed limiter is unavailable.
PARTNER_LIMIT_RESPONSES = RATE_LIMIT_FAIL_CLOSED_RESPONSES


def policy_for(method: str) -> RateLimitPolicy:
    return (
        PARTNER_READ_POLICY if method.upper() in READ_METHODS else PARTNER_WRITE_POLICY
    )


async def enforce_key_limit(
    limiter: RateLimiter, key_id: str, method: str
) -> Response | None:
    """The response that refuses the request (429, or 503 when a fail-closed limiter is down); ``None`` to go on."""
    policy = policy_for(method)
    try:
        decision = await limiter.hit(f"{policy.scope}:key:{key_id}", policy)
    except Exception as exc:
        if policy.fail_closed:
            logger.error(
                "partner rate limiter unavailable, rejecting request: key_id=%s",
                key_id,
                exc_info=exc,
            )
            return error_response(
                503,
                UNAVAILABLE_MESSAGE,
                {"Retry-After": str(FAIL_CLOSED_RETRY_AFTER_SECONDS)},
            )
        logger.warning(
            "partner rate limiter unavailable, allowing request: key_id=%s",
            key_id,
            exc_info=exc,
        )
        report_exception(exc)
        return None
    if decision.allowed:
        return None
    return error_response(
        429,
        TOO_MANY_REQUESTS_MESSAGE,
        {
            **rate_limit_headers(decision),
            "Retry-After": str(retry_after_seconds(decision)),
        },
    )
