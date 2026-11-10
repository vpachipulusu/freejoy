from collections.abc import AsyncIterator, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, Protocol

from sqlalchemy import NullPool
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings
from app.core.db_url import to_async_target


def build_engine(
    database_url: str, extra_connect_args: Mapping[str, Any] | None = None
) -> AsyncEngine:
    """Create the async engine, keeping the TLS mode from the URL (e.g. sslmode=require).

    Every engine gets the pooler-safe connect args from ``to_async_target``
    (statement cache off, bounded connect timeout).

    Only this module (and ``alembic_migrations/env.py`` and the tests) may call it: ruff ``TID251``
    in ``pyproject.toml`` bans ``build_engine`` and the SQLAlchemy engine factories everywhere else.
    """
    target = to_async_target(database_url)
    # Disable connection pooling for serverless environments like Vercel
    return create_async_engine(
        target.url,
        poolclass=NullPool,
        connect_args={**target.connect_args, **(extra_connect_args or {})},
    )


engine = build_engine(settings.DATABASE_URL)

async_session_maker = async_sessionmaker(
    engine, expire_on_commit=settings.EXPIRE_ON_COMMIT
)


class SessionProvider(Protocol):
    """Hands out database sessions to repositories (ADR-004).

    Repositories never create sessions: they receive one from a provider.
    Routing to another database plugs in by supplying another implementation of
    this interface, without changing any repository.
    """

    def session(self) -> AbstractAsyncContextManager[AsyncSession]:
        """Open a session; it is closed when the context exits."""


class DefaultSessionProvider:
    """Sessions on the platform database, one per request (NullPool, no reuse)."""

    def __init__(self, maker: async_sessionmaker[AsyncSession]) -> None:
        self._maker = maker

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self._maker() as session:
            yield session
