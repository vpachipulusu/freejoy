"""Partner rate limits (ADR-007, ADR-013), applied by the sub-app itself so a route cannot forget them.

One dependency on the sub-app chooses the policy from the method: a read (``GET``, ``HEAD``, ``OPTIONS``) is
``PARTNER_READ_POLICY`` (fails open), anything else is ``PARTNER_WRITE_POLICY`` (fails closed). Both are keyed
by the key id (``rl:partner_read:key:<key id>``), never by client address. Failed authentications are counted
separately, by ``PartnerAuthenticator``, with ``PARTNER_AUTH_FAILURE_POLICY`` (fails closed).
"""

from typing import Annotated

from fastapi import Depends, Request, Response

from app.core.rate_limit import (
    PARTNER_READ_POLICY,
    PARTNER_WRITE_POLICY,
    RateLimiter,
    RateLimitPolicy,
)
from app.core.rate_limit_enforcement import (
    RATE_LIMIT_FAIL_CLOSED_RESPONSES,
    enforce_rate_limit,
)
from app.core.rate_limiter_provider import get_rate_limiter
from app.partner.auth import PartnerContextDep

READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# Documented on the sub-app: a read can answer 429, a write 429 or 503.
PARTNER_LIMIT_RESPONSES = RATE_LIMIT_FAIL_CLOSED_RESPONSES

LimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]


def policy_for(method: str) -> RateLimitPolicy:
    return (
        PARTNER_READ_POLICY if method.upper() in READ_METHODS else PARTNER_WRITE_POLICY
    )


async def limit_partner_requests(
    request: Request,
    response: Response,
    limiter: LimiterDep,
    context: PartnerContextDep,
) -> None:
    policy = policy_for(request.method)
    await enforce_rate_limit(
        request, response, limiter, policy, [f"{policy.scope}:key:{context.key_id}"]
    )
