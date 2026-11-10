"""The partner sub-app and the wrapper that is mounted at ``/partner/v1`` (ADR-013)."""

from fastapi import FastAPI

from app.core.config import settings
from app.core.errors import ERROR_RESPONSES, register_error_handlers
from app.partner.surface import PartnerSurface

PARTNER_API_TITLE = "Freejoy Partner API"
# The partner contract is versioned on its own (ADR-008 amendment), not with the internal API.
PARTNER_API_VERSION = "1.0.0"


def create_partner_api() -> FastAPI:
    """The sub-app itself: no docs, no schema route, no slash redirects, the shared error envelope."""
    api = FastAPI(
        title=PARTNER_API_TITLE,
        version=PARTNER_API_VERSION,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        redirect_slashes=False,
        responses=ERROR_RESPONSES,
    )
    register_error_handlers(api)
    return api


def create_partner_app() -> PartnerSurface:
    """What ``app.main`` mounts: the sub-app inside its surface wrapper."""
    return PartnerSurface(
        create_partner_api(), enabled=lambda: settings.PARTNER_API_ENABLED
    )
