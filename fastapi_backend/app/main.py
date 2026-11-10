import logging

from fastapi import FastAPI

from app.api.ids import simple_generate_unique_route_id
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.errors import ERROR_RESPONSES, register_error_handlers
from app.core.logging import configure_logging
from app.core.request_id import RequestIdMiddleware
from app.core.sentry import init_sentry
from app.partner.app import create_partner_app
from app.partner.mount import PartnerMount
from app.partner.surface import PARTNER_PREFIX

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    configure_logging(settings.LOG_LEVEL)
    # Before the app is built so the SDK can instrument Starlette. No-op without a DSN.
    init_sentry(settings)

    # The backend URL is public, so production serves no schema or docs UI
    # (ADR-003). `app.openapi()` still works for the schema generation command.
    # Testing-phase exception: API_DOCS_ENABLED serves them in production too.
    docs_enabled = settings.relaxed_environment or settings.API_DOCS_ENABLED
    if docs_enabled and not settings.relaxed_environment:
        logger.warning(
            "API docs (/docs, /redoc, schema) are served in a non-relaxed environment "
            "because API_DOCS_ENABLED is set; remove it at go-live"
        )
    application = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        generate_unique_id_function=simple_generate_unique_route_id,
        openapi_url=settings.OPENAPI_URL if docs_enabled else None,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        responses=ERROR_RESPONSES,
    )

    # No CORSMiddleware on purpose (ADR-003): only the Next.js server calls this
    # API, so no browser origin is ever allowed.
    application.add_middleware(RequestIdMiddleware)
    register_error_handlers(application)
    application.include_router(api_router)
    # The one mount (ADR-013): partner handlers live only here, behind their own surface wrapper.
    application.routes.append(
        PartnerMount(PARTNER_PREFIX, app=create_partner_app(), name="partner")
    )
    return application


app = create_app()
