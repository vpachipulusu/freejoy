"""FastAPI dependencies: where the database session provider is injected."""

import logging
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import (
    DefaultSessionProvider,
    SessionProvider,
    async_session_maker,
)
from app.core.email_provider import EmailProvider, build_email_provider
from app.core.rate_limiter_provider import get_rate_limiter
from app.repositories.health import HealthRepository
from app.services.health import HealthService

logger = logging.getLogger(__name__)

_default_provider = DefaultSessionProvider(async_session_maker)


def get_session_provider() -> SessionProvider:
    """The one place that decides which provider repositories get.

    Tests override this dependency with a fake; a product that routes to more than
    one database swaps or extends it.
    """
    return _default_provider


async def get_session(
    provider: Annotated[SessionProvider, Depends(get_session_provider)],
) -> AsyncIterator[AsyncSession]:
    async with provider.session() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]


def allow_sample_provider() -> bool:
    """Whether the ``sample`` affiliate provider may be followed (local, test, preview only).

    The offer page flag and the outbound click must read the same value, so both services are built
    from this one function.
    """
    return settings.relaxed_environment


def get_health_service(session: SessionDep) -> HealthService:
    return HealthService(HealthRepository(session))


HealthServiceDep = Annotated[HealthService, Depends(get_health_service)]


_email_provider: EmailProvider | None = None


def get_email_provider() -> EmailProvider:
    """The alerts email provider (ADR-012); the disabled one unless EMAIL_PROVIDER is set.

    Tests override this dependency with a ``FakeEmailProvider``.
    """
    global _email_provider
    if _email_provider is None:
        _email_provider = build_email_provider(settings)
        if settings.EMAIL_PROVIDER == "":
            logger.warning("EMAIL_PROVIDER is not set: no email will be sent")
    return _email_provider


EmailProviderDep = Annotated[EmailProvider, Depends(get_email_provider)]

# ``get_rate_limiter`` lives in app.core.rate_limiter_provider (the partner surface uses it too); tests
# override it from here, which is the same function object.
__all__ = [
    "EmailProviderDep",
    "HealthServiceDep",
    "SessionDep",
    "get_email_provider",
    "get_health_service",
    "get_rate_limiter",
    "get_session",
    "get_session_provider",
]
