"""Where the partner surface gets its database session (ADR-013).

Every partner route and the partner authentication get their session from here, and from nowhere
else: the provider is bound to ``PARTNER_DATABASE_URL`` (the restricted role), never to the owner
``DATABASE_URL``. The provider is built the first time it is asked for, not at import and not in a
lifespan hook (a mounted sub-app's lifespan never runs).

Tests replace ``get_partner_session_provider``, ``build_key_store`` and ``build_click_store`` on this module.
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import PartnerSessionProvider, SessionProvider
from app.partner.clicks_report import PartnerClickReader
from app.partner.clicks_store import PartnerClickStore
from app.partner.keys import KeyLookup, PartnerKeyStore

_provider: SessionProvider | None = None


def get_partner_session_provider() -> SessionProvider:
    global _provider
    if _provider is None:
        url = settings.PARTNER_DATABASE_URL
        _provider = PartnerSessionProvider(url.get_secret_value() if url else None)
    return _provider


def build_key_store(session: AsyncSession) -> KeyLookup:
    return PartnerKeyStore(session)


def build_click_store(session: AsyncSession) -> PartnerClickReader:
    return PartnerClickStore(session)


async def get_partner_session() -> AsyncIterator[AsyncSession]:
    async with get_partner_session_provider().session() as session:
        yield session


def get_key_store(
    session: Annotated[AsyncSession, Depends(get_partner_session)],
) -> KeyLookup:
    return build_key_store(session)


KeyStoreDep = Annotated[KeyLookup, Depends(get_key_store)]


def get_click_store(
    session: Annotated[AsyncSession, Depends(get_partner_session)],
) -> PartnerClickReader:
    return build_click_store(session)


ClickStoreDep = Annotated[PartnerClickReader, Depends(get_click_store)]
