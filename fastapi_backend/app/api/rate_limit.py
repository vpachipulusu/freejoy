"""Rate limit dependencies (ADR-007), attached per route, not as middleware.

Authenticated routes use ``limit_by_subject`` with a dependency that resolves who
is acting (user or organisation). Unauthenticated sign-in, sign-up and
password reset use ``limit_auth_attempts``.
"""

import hashlib
import hmac
import ipaddress
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Request, Response

from app.api.deps import get_rate_limiter
from app.core.config import settings
from app.core.rate_limit import (
    AUTH_POLICY,
    RateLimiter,
    RateLimitPolicy,
    client_bucket,
)
from app.core.rate_limit_enforcement import (
    FAIL_CLOSED_RETRY_AFTER_SECONDS,
    RATE_LIMIT_FAIL_CLOSED_RESPONSES,
    RATE_LIMIT_RESPONSES,
    TOO_MANY_REQUESTS_MESSAGE,
    UNAVAILABLE_MESSAGE,
    enforce_rate_limit,
    enforce_rate_limits,
)

# The enforcement helpers moved to app.core.rate_limit_enforcement (the partner surface uses them too);
# these names stay importable from here.
__all__ = [
    "FAIL_CLOSED_RETRY_AFTER_SECONDS",
    "RATE_LIMIT_FAIL_CLOSED_RESPONSES",
    "RATE_LIMIT_RESPONSES",
    "TOO_MANY_REQUESTS_MESSAGE",
    "UNAVAILABLE_MESSAGE",
    "RateLimitSubject",
    "bucket_of",
    "client_ip",
    "client_key",
    "email_digest",
    "enforce_rate_limit",
    "enforce_rate_limits",
    "forwarded_client_ip",
    "limit_auth_attempts",
    "limit_by_client_ip",
    "limit_by_client_ip_and_email",
    "limit_by_subject",
]

logger = logging.getLogger("yourapp.rate_limit")

CLIENT_IP_HEADER = "X-Client-IP"


@dataclass(frozen=True)
class RateLimitSubject:
    """Who is acting: key ``rl:{scope}:{kind}:{id}``."""

    kind: Literal["user", "org"]
    id: str


RateLimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]


def limit_by_subject(
    policy: RateLimitPolicy,
    subject_dependency: Callable[..., RateLimitSubject | Awaitable[RateLimitSubject]],
) -> Callable[..., Awaitable[None]]:
    """Dependency factory for authenticated routes (``API_POLICY`` or ``WEBHOOK_POLICY``)."""

    async def dependency(
        request: Request,
        response: Response,
        limiter: RateLimiterDep,
        subject: Annotated[RateLimitSubject, Depends(subject_dependency)],
    ) -> None:
        identifier = f"{policy.scope}:{subject.kind}:{subject.id}"
        await enforce_rate_limit(request, response, limiter, policy, [identifier])

    return dependency


def forwarded_client_ip(request: Request) -> str | None:
    """The validated ``X-Client-IP`` from an internal-key caller, else None."""
    if not getattr(request.state, "internal_authenticated", False):
        return None
    forwarded = request.headers.get(CLIENT_IP_HEADER, "").strip()
    try:
        return str(ipaddress.ip_address(forwarded))
    except ValueError:
        return None


def client_ip(request: Request) -> str:
    """The end-user IP forwarded by Next.js in ``X-Client-IP``, exactly as sent (audit rows use it).

    Only a validated internal service key may establish the forwarding trust boundary.
    Otherwise use the direct peer; ignore inbound X-Forwarded-For. For rate-limit keys use
    ``client_key``, which groups IPv6 by /64.
    """
    forwarded = forwarded_client_ip(request)
    if forwarded is not None:
        return forwarded
    # Missing or malformed: fall back to the direct peer so the call is still limited.
    return request.client.host if request.client else "unknown"


def bucket_of(address: str) -> str:
    """``client_bucket`` that leaves a non-address (the ``unknown`` peer) as it is."""
    try:
        return client_bucket(address)
    except ValueError:
        return address


def client_key(request: Request) -> str:
    """The rate-limit key part for the end user: IPv4 as is, IPv6 grouped by /64."""
    return bucket_of(client_ip(request))


def limit_by_client_ip(policy: RateLimitPolicy) -> Callable[..., Awaitable[None]]:
    """Dependency factory for unauthenticated routes with no user to key on.

    Keyed by the forwarded client IP (see ``client_key``). It sets rate-limit headers on
    the injected response; a route that returns its own cacheable ``Response`` drops
    them, so a shared cache never stores one client's counters.
    """

    async def dependency(
        request: Request, response: Response, limiter: RateLimiterDep
    ) -> None:
        identifier = f"{policy.scope}:ip:{client_key(request)}"
        await enforce_rate_limit(request, response, limiter, policy, [identifier])

    return dependency


def email_digest(email: str) -> str:
    key = settings.RATE_LIMIT_EMAIL_KEY
    if not key:
        raise HTTPException(503, UNAVAILABLE_MESSAGE, {"Retry-After": "5"})
    return hmac.new(
        key.get_secret_value().encode(), email.strip().lower().encode(), hashlib.sha256
    ).hexdigest()


async def _no_email() -> None:
    return None


def limit_by_client_ip_and_email(
    ip_policy: RateLimitPolicy,
    email_policy: RateLimitPolicy,
    email_dependency: Callable[..., str | Awaitable[str]],
) -> Callable[..., Awaitable[None]]:
    """Dependency factory for public routes that act on an address the caller supplies.

    Limits by client IP (``ip_policy``) and by the keyed HMAC of the address, never the address itself
    (``email_policy``), so one address cannot be mailed in bulk from many IPs and one IP cannot mail many
    addresses.
    """

    async def dependency(
        request: Request,
        response: Response,
        limiter: RateLimiterDep,
        email: Annotated[str, Depends(email_dependency)],
    ) -> None:
        await enforce_rate_limits(
            request,
            response,
            limiter,
            [
                (ip_policy, f"{ip_policy.scope}:ip:{client_key(request)}"),
                (email_policy, f"{email_policy.scope}:email:{email_digest(email)}"),
            ],
        )

    return dependency


def limit_auth_attempts(
    email_dependency: Callable[..., str | None | Awaitable[str | None]] | None = None,
) -> Callable[..., Awaitable[None]]:
    """Dependency factory for sign-in, sign-up and password reset (``AUTH_POLICY``).

    Limits by client IP and, when ``email_dependency`` yields an email, by the
    keyed HMAC of that email too. Fails closed when Redis is down.
    """

    async def dependency(
        request: Request,
        response: Response,
        limiter: RateLimiterDep,
        email: Annotated[str | None, Depends(email_dependency or _no_email)],
    ) -> None:
        identifiers = [f"{AUTH_POLICY.scope}:ip:{client_key(request)}"]
        if email:
            identifiers.append(f"{AUTH_POLICY.scope}:email:{email_digest(email)}")
        await enforce_rate_limit(request, response, limiter, AUTH_POLICY, identifiers)

    return dependency
