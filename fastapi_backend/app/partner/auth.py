"""Partner authentication (ADR-013): who is calling, decided before any route is chosen.

``PartnerAuthenticator`` is run by ``PartnerSurface`` for every request under ``/partner/v1`` while the surface
is on, so an unknown path, a wrong method and a raw route get the same 401 as a real route: nothing about
which routes exist is learned without a valid key. It reads ``Authorization: Bearer ...`` and nothing else
(never a query parameter, never a forwarded client-IP header), and hands the sub-app a frozen
``PartnerContext``. ``require_partner_key`` is the sub-app's own dependency: it returns the context the surface
established and answers 401 if there is none, so a route cannot run unauthenticated even if the sub-app were
ever served without the surface.

Unknown, revoked, malformed and wrong-environment keys, a missing header, an inactive merchant and a key
presented with another credential (``X-Internal-Key``, ``X-Session-Token``, ``Cookie``) all give the same
401 with the same body and headers. The cause is logged by key id only (never the key, the header or the hash).
Failed attempts are counted per presented key id (one shared bucket for malformed keys) and answer 429 past the
limit; a valid key is never counted, and is never refused because of failures.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Final

from fastapi import HTTPException, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import Headers
from starlette.responses import Response
from starlette.types import Scope

from app.core.database import SessionProvider
from app.core.errors import error_response
from app.core.partner_keys import ParsedKey, key_matches, parse_key
from app.core.rate_limit import PARTNER_AUTH_FAILURE_POLICY, RateLimiter
from app.core.rate_limit_enforcement import (
    FAIL_CLOSED_RETRY_AFTER_SECONDS,
    TOO_MANY_REQUESTS_MESSAGE,
    UNAVAILABLE_MESSAGE,
    rate_limit_headers,
    retry_after_seconds,
)
from app.partner.keys import KeyStore

logger = logging.getLogger("yourapp.partner")

UNAUTHORIZED_MESSAGE = "Unauthorized"
CHALLENGE_HEADERS = {"WWW-Authenticate": "Bearer"}
CONTEXT_SCOPE_KEY: Final = "freejoy.partner_context"
MALFORMED_BUCKET = f"{PARTNER_AUTH_FAILURE_POLICY.scope}:malformed"
# Credentials of the internal API and of a signed-in user. A partner key presented with any of them is refused.
OTHER_CREDENTIAL_HEADERS = ("x-internal-key", "x-session-token", "cookie")
_BEARER_PREFIX = "bearer "


@dataclass(frozen=True)
class PartnerContext:
    """The authenticated caller: the key (by id) and the one merchant it belongs to."""

    key_id: str
    merchant_id: str


Outcome = PartnerContext | Response
Authenticator = Callable[[Scope], Awaitable[Outcome]]
SessionProviderGetter = Callable[[], SessionProvider]
KeyStoreFactory = Callable[[AsyncSession], KeyStore]
LimiterGetter = Callable[[], RateLimiter]
EnvironmentGetter = Callable[[], str | None]


def bearer_token(headers: Headers) -> str | None:
    """The token of the one ``Authorization: Bearer`` header; ``None`` if absent, repeated or another scheme."""
    values = headers.getlist("authorization")
    if len(values) != 1 or not values[0].lower().startswith(_BEARER_PREFIX):
        return None
    return values[0][len(_BEARER_PREFIX) :]


def unauthorized() -> Response:
    return error_response(401, UNAUTHORIZED_MESSAGE, CHALLENGE_HEADERS)


def unavailable() -> Response:
    return error_response(
        503,
        UNAVAILABLE_MESSAGE,
        {"Retry-After": str(FAIL_CLOSED_RETRY_AFTER_SECONDS)},
    )


class PartnerAuthenticator:
    def __init__(
        self,
        *,
        sessions: SessionProviderGetter,
        stores: KeyStoreFactory,
        limiter: LimiterGetter,
        environment: EnvironmentGetter,
    ) -> None:
        self._sessions = sessions
        self._stores = stores
        self._limiter = limiter
        self._environment = environment

    async def __call__(self, scope: Scope) -> Outcome:
        """The caller's context, or the response to send instead (401, 429 or 503)."""
        try:
            return await self._authenticate(Headers(scope=scope))
        except Exception as exc:  # noqa: BLE001 - nothing here may escape as an unmarked 500
            logger.error("partner authentication failed: error=%s", type(exc).__name__)
            return unavailable()

    async def _authenticate(self, headers: Headers) -> Outcome:
        token = bearer_token(headers)
        environment = self._environment()
        parsed = parse_key(token, environment) if token and environment else None
        if parsed is None:
            return await self._refuse(None, "malformed")
        if any(name in headers for name in OTHER_CREDENTIAL_HEADERS):
            return await self._refuse(parsed.key_id, "mixed_credentials")
        return await self._check(parsed)

    async def _check(self, parsed: ParsedKey) -> Outcome:
        async with self._sessions().session() as session:
            store = self._stores(session)
            stored = await store.find(parsed.key_id)
            genuine = key_matches(
                parsed.digest, stored.key_hash if stored is not None else None
            )
            if stored is None:
                return await self._refuse(parsed.key_id, "unknown")
            if not genuine:
                return await self._refuse(parsed.key_id, "wrong_secret")
            if not stored.usable:
                return await self._refuse(parsed.key_id, "revoked_or_inactive")
            await self._record_use(store, session, parsed.key_id)
            return PartnerContext(key_id=stored.key_id, merchant_id=stored.merchant_id)

    async def _record_use(
        self, store: KeyStore, session: AsyncSession, key_id: str
    ) -> None:
        """Best effort: a failed ``last_used_at`` update never refuses a valid key."""
        try:
            await store.touch(key_id)
            await session.commit()
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            logger.warning(
                "partner key use not recorded: key_id=%s error=%s",
                key_id,
                type(exc).__name__,
            )

    async def _refuse(self, key_id: str | None, reason: str) -> Response:
        if key_id is not None:
            logger.info(
                "partner authentication refused: key_id=%s reason=%s", key_id, reason
            )
        bucket = (
            MALFORMED_BUCKET
            if key_id is None
            else f"{PARTNER_AUTH_FAILURE_POLICY.scope}:key:{key_id}"
        )
        try:
            decision = await self._limiter().hit(bucket, PARTNER_AUTH_FAILURE_POLICY)
        except Exception as exc:  # noqa: BLE001 - the policy fails closed
            logger.error(
                "partner authentication limiter unavailable: error=%s",
                type(exc).__name__,
            )
            return unavailable()
        if decision.allowed:
            return unauthorized()
        return error_response(
            429,
            TOO_MANY_REQUESTS_MESSAGE,
            {
                **rate_limit_headers(decision),
                "Retry-After": str(retry_after_seconds(decision)),
            },
        )


bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="PartnerKey",
    description="Your partner API key: fj_<env>_<key_id>_<secret>. Sent only as a bearer token.",
)


def require_partner_key(
    request: Request,
    _credentials: Annotated[
        HTTPAuthorizationCredentials | None, Security(bearer_scheme)
    ] = None,
) -> PartnerContext:
    """The sub-app's authentication dependency: hands routes the context the surface established.

    ``PartnerSurface`` authenticates before routing and stores the context in the scope. A request that
    reaches a route without one (the sub-app served without its surface) is refused with the same 401.
    """
    context = request.scope.get(CONTEXT_SCOPE_KEY)
    if not isinstance(context, PartnerContext):
        raise HTTPException(401, UNAUTHORIZED_MESSAGE, CHALLENGE_HEADERS)
    return context


PartnerContextDep = Annotated[PartnerContext, Security(require_partner_key)]
