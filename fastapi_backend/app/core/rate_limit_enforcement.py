"""Applying rate limit policies to a request (ADR-007), shared by the internal API and the partner surface.

These are the helpers ``app.api.rate_limit`` always had, moved here (the three private ones became public, and
``handle_outage`` takes the request id instead of the request) so that ``app.partner`` (which may
import only ``app.core``) applies a policy the same way: first denial raises 429, a Redis outage follows the
policy's fail mode (closed: 503, open: log, report and let through). ``app.api.rate_limit`` re-exports every name.
"""

import logging
import math
import time
from typing import Any

from fastapi import HTTPException, Request, Response, status

from app.core.errors import ErrorResponse
from app.core.rate_limit import RateLimitDecision, RateLimiter, RateLimitPolicy
from app.core.sentry import report_exception

logger = logging.getLogger("yourapp.rate_limit")

TOO_MANY_REQUESTS_MESSAGE = "Too many requests"
UNAVAILABLE_MESSAGE = "Service temporarily unavailable"
FAIL_CLOSED_RETRY_AFTER_SECONDS = 5

# Add to a route's ``responses=`` so the OpenAPI schema documents these errors.
RATE_LIMIT_RESPONSES: dict[int | str, dict[str, Any]] = {
    429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
}
RATE_LIMIT_FAIL_CLOSED_RESPONSES: dict[int | str, dict[str, Any]] = {
    **RATE_LIMIT_RESPONSES,
    503: {"model": ErrorResponse, "description": "Rate limiter unavailable"},
}


def rate_limit_headers(decision: RateLimitDecision) -> dict[str, str]:
    return {
        "X-RateLimit-Limit": str(decision.limit),
        "X-RateLimit-Remaining": str(decision.remaining),
        "X-RateLimit-Reset": str(math.ceil(decision.reset_at)),
    }


def retry_after_seconds(decision: RateLimitDecision) -> int:
    return max(1, math.ceil(decision.reset_at - time.time()))


def too_many_requests(decision: RateLimitDecision) -> HTTPException:
    headers = {
        **rate_limit_headers(decision),
        "Retry-After": str(retry_after_seconds(decision)),
    }
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS, TOO_MANY_REQUESTS_MESSAGE, headers
    )


def handle_outage(request_id: object, policy: RateLimitPolicy, exc: Exception) -> None:
    """Redis could not answer. Fail closed (503) or open (log and let through)."""
    if policy.fail_closed:
        logger.error(
            "rate limiter unavailable, rejecting request",
            exc_info=exc,
            extra={"request_id": request_id},
        )
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            UNAVAILABLE_MESSAGE,
            {"Retry-After": str(FAIL_CLOSED_RETRY_AFTER_SECONDS)},
        )
    logger.warning(
        "rate limiter unavailable, allowing request",
        exc_info=exc,
        extra={"request_id": request_id},
    )
    report_exception(exc)


async def enforce_rate_limits(
    request: Request,
    response: Response,
    limiter: RateLimiter,
    checks: list[tuple[RateLimitPolicy, str]],
) -> None:
    """Hit every (policy, identifier) pair in order; the first denial raises 429.

    Each pair follows its own policy's fail mode on a Redis outage. Headers show the tightest decision.
    """
    tightest: RateLimitDecision | None = None
    for policy, identifier in checks:
        try:
            decision = await limiter.hit(identifier, policy)
        except Exception as exc:  # noqa: BLE001 - any adapter failure follows the fail mode
            request_id = request.scope.get("state", {}).get("request_id")
            handle_outage(request_id, policy, exc)
            return
        if not decision.allowed:
            raise too_many_requests(decision)
        if tightest is None or decision.remaining < tightest.remaining:
            tightest = decision
    if tightest is not None:
        response.headers.update(rate_limit_headers(tightest))


async def enforce_rate_limit(
    request: Request,
    response: Response,
    limiter: RateLimiter,
    policy: RateLimitPolicy,
    identifiers: list[str],
) -> None:
    """Hit every identifier under one policy; the first denial raises 429. Headers show the tightest."""
    await enforce_rate_limits(
        request, response, limiter, [(policy, identifier) for identifier in identifiers]
    )
