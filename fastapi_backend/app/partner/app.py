"""The partner sub-app and the wrapper that is mounted at ``/partner/v1`` (ADR-013)."""

from typing import Any

from fastapi import Depends, FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import settings
from app.core.errors import register_error_handlers
from app.core.partner_keys import running_environment
from app.core.rate_limiter_provider import get_rate_limiter
from app.partner import deps
from app.partner.auth import PartnerAuthenticator, require_partner_key
from app.partner.body_errors import partner_http_exception_handler
from app.partner.clicks_routes import partner_validation_handler
from app.partner.clicks_routes import router as clicks_router
from app.partner.error_models import PartnerErrorResponse
from app.partner.failures import register_failure_handlers
from app.partner.mount import PartnerMount
from app.partner.offer_routes import router as offers_router
from app.partner.routes import router
from app.partner.surface import PARTNER_PREFIX, PartnerSurface

PARTNER_API_TITLE = "Freejoy Partner API"
# The partner contract is versioned on its own (ADR-008 amendment), not with the internal API.
PARTNER_API_VERSION = "1.0.0"


# Answers every partner route can give besides its own. 401 is decided before routing (the surface) and again by
# ``require_partner_key``; 429 and 503 come from the rate limits and the database, also before routing.
# The descriptions are written for a retailer: the partner document is what gets shared.
PARTNER_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {
        "model": PartnerErrorResponse,
        "description": "Missing, unknown, revoked or malformed key",
    },
    422: {"model": PartnerErrorResponse, "description": "Invalid request"},
    429: {
        "model": PartnerErrorResponse,
        "description": "Too many requests. Wait the number of seconds in the Retry-After header.",
    },
    500: {"model": PartnerErrorResponse, "description": "Internal server error"},
    503: {
        "model": PartnerErrorResponse,
        "description": "Service temporarily unavailable. Try again after the number of seconds in the Retry-After header.",
    },
}


def create_partner_api() -> FastAPI:
    """The sub-app itself: no docs, no schema route, no slash redirects, the shared error envelope.

    Authentication is attached here, to the app, so no route can be added without it. The per-key rate limit is
    applied by the surface right after authentication (``app.partner.limits``), so it needs no dependency.
    """
    api = FastAPI(
        title=PARTNER_API_TITLE,
        version=PARTNER_API_VERSION,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        redirect_slashes=False,
        responses=PARTNER_RESPONSES,
        dependencies=[Depends(require_partner_key)],
    )
    register_error_handlers(api)
    # The shared 422, except that a refused ``from`` or ``to`` of the clicks route (and only that route: the handler
    # checks the matched path) gets its own fixed text and code. Every other route, the offer routes included, keeps
    # the shared plain "Invalid request" (``tests/api/partner/test_partner_offer_routes.py`` pins it).
    api.add_exception_handler(
        RequestValidationError,
        partner_validation_handler,  # type: ignore[arg-type]  # Starlette types handlers on Exception; as in register_error_handlers
    )
    # A database that cannot answer inside any partner route is the same fixed 503 as in authentication.
    register_failure_handlers(api)
    # A body the parser rejects (invalid UTF-8, nesting, a huge integer) is the plain 422, not FastAPI's own 400.
    api.add_exception_handler(
        StarletteHTTPException,
        partner_http_exception_handler,  # type: ignore[arg-type]
    )
    api.include_router(router)
    api.include_router(clicks_router)
    api.include_router(offers_router)
    return api


def create_partner_authenticator() -> PartnerAuthenticator:
    """The authenticator the surface runs. Everything it needs is looked up per request (never captured at
    import or in a lifespan hook), so a mounted sub-app needs no startup and tests can replace each piece."""
    return PartnerAuthenticator(
        sessions=lambda: deps.get_partner_session_provider(),
        stores=lambda session: deps.build_key_store(session),
        limiter=lambda: get_rate_limiter(),
        environment=lambda: running_environment(
            settings.VERCEL_ENV, settings.ENVIRONMENT
        ),
    )


def create_partner_app() -> PartnerSurface:
    """What ``app.main`` mounts: the sub-app inside its surface wrapper."""
    return PartnerSurface(
        create_partner_api(),
        enabled=lambda: settings.PARTNER_API_ENABLED,
        authenticator=create_partner_authenticator(),
    )


def install_partner_surface(application: FastAPI) -> None:
    """Mount the surface on ``application`` as its FIRST route.

    First, so no main-app route (a catch-all, or a pattern such as ``/{a}/v1/{b}``) can be tried
    before the mount and answer a ``/partner/v1/...`` request without the surface headers.
    """
    application.router.routes.insert(
        0, PartnerMount(PARTNER_PREFIX, app=create_partner_app(), name="partner")
    )
