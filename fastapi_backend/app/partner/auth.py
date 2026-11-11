"""Partner authentication (ADR-013): who is calling, decided before any route is chosen.

``PartnerAuthenticator`` is run by ``PartnerSurface`` for every request under ``/partner/v1`` while the surface
is on, so an unknown path, a wrong method and a raw route get the same 401 as a real route: nothing about
which routes exist is learned without a valid key. It reads ``Authorization: Bearer ...`` and nothing else
(never a query parameter, never a forwarded client-IP header), and hands the sub-app a frozen
``PartnerContext``. ``require_partner_key`` is the sub-app's own dependency: it returns the context the surface
established and answers 401 if there is none, so a route cannot run unauthenticated even if the sub-app were
ever served without the surface.

A missing header, an unparseable value and a key presented with another credential (``X-Internal-Key``,
``X-Session-Token``, ``Cookie``) cost nothing: they get the plain 401 at once and are never counted or limited.
Any well-formed key (whatever ``<env>`` it names) takes one path: look the id up, compare the digest in constant
time (against a dummy when the id is unknown), then decide. Unknown ids, wrong secrets, a key for another
environment, revoked keys and inactive merchants all give the same 401 and are counted in the presented key id's
failure bucket; the answer past the limit is the same 429 for all of them. The cause is logged by key id only
(never the key, the header or the hash).

What the failure limit does NOT do: bound database work. Every well-formed key costs one connection and one
SELECT, counted or not (counting it first would let anyone who knows a key id lock its owner out). Unauthenticated
lookups are bounded only by the edge rate rule and the role's connection limit (ADR-013 launch preconditions).
No limiter call runs while a database session is open. A valid key is never counted as a failure.

After a successful check the per-key read or write limit is applied once, here, before routing
(``app.partner.limits``), so it also covers requests that match no route.
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
from app.core.partner_keys import ParsedKey, key_matches, parse_well_formed_key
from app.core.rate_limit import PARTNER_AUTH_FAILURE_POLICY, RateLimiter
from app.core.rate_limit_enforcement import (
    FAIL_CLOSED_RETRY_AFTER_SECONDS,
    TOO_MANY_REQUESTS_MESSAGE,
    UNAVAILABLE_MESSAGE,
    rate_limit_headers,
    retry_after_seconds,
)
from app.core.sentry import report_exception
from app.partner.keys import KeyLookup
from app.partner.limits import enforce_key_limit

logger = logging.getLogger("yourapp.partner")

UNAUTHORIZED_MESSAGE = "Unauthorized"
CHALLENGE_HEADERS = {"WWW-Authenticate": "Bearer"}
CONTEXT_SCOPE_KEY: Final = "freejoy.partner_context"
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
KeyLookupFactory = Callable[[AsyncSession], KeyLookup]
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
        stores: KeyLookupFactory,
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
            return await self._authenticate(scope)
        except Exception as exc:
            # The traceback and the exception reporter: a 503 alone would hide a bug or a missing grant.
            # The key, its hash and the header are never passed down here, so none can be in the record.
            logger.error("partner authentication failed", exc_info=exc)
            report_exception(exc)
            return unavailable()

    async def _authenticate(self, scope: Scope) -> Outcome:
        headers = Headers(scope=scope)
        token = bearer_token(headers)
        parsed = parse_well_formed_key(token) if token else None
        if parsed is None:
            return unauthorized()  # costs nothing, so it is never counted or limited
        if any(name in headers for name in OTHER_CREDENTIAL_HEADERS):
            logger.info(
                "partner authentication refused: key_id=%s reason=mixed_credentials",
                parsed.key_id,
            )
            return unauthorized()
        decided = await self._decide(parsed)
        if isinstance(decided, str):
            return await self._refuse(parsed.key_id, decided)
        refusal = await enforce_key_limit(
            self._limiter(), decided.key_id, scope["method"]
        )
        return refusal if refusal is not None else decided

    async def _decide(self, parsed: ParsedKey) -> PartnerContext | str:
        """The context for a genuine key, else the reason. The session is closed before the caller goes on."""
        async with self._sessions().session() as session:
            store = self._stores(session)
            stored = await store.find(parsed.key_id)
            # Always compared (against a dummy for an unknown id), and the environment is judged only after
            # the comparison, so it selects neither a faster path nor a different bucket.
            genuine = key_matches(
                parsed.digest, stored.key_hash if stored is not None else None
            )
            genuine = genuine and parsed.environment == self._environment()
            if stored is None:
                return "unknown"
            if not genuine:
                return "wrong_secret_or_environment"
            if not stored.usable:
                return "revoked_or_inactive"
            if stored.touch_due:
                await self._record_use(store, session, parsed.key_id)
            return PartnerContext(key_id=stored.key_id, merchant_id=stored.merchant_id)

    async def _record_use(
        self, store: KeyLookup, session: AsyncSession, key_id: str
    ) -> None:
        """Best effort: a failed ``last_used_at`` update never refuses a valid key."""
        try:
            await store.touch(key_id)
            await session.commit()
        except Exception as exc:  # noqa: BLE001 - never refuses a valid key
            await session.rollback()
            logger.warning(
                "partner key use not recorded: key_id=%s error=%s",
                key_id,
                type(exc).__name__,
            )

    async def _refuse(self, key_id: str, reason: str) -> Response:
        logger.info(
            "partner authentication refused: key_id=%s reason=%s", key_id, reason
        )
        bucket = f"{PARTNER_AUTH_FAILURE_POLICY.scope}:key:{key_id}"
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
