from collections.abc import AsyncIterator, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, Protocol

from sqlalchemy import NullPool, text
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


class PartnerDatabaseUnavailable(RuntimeError):
    """The partner surface has no restricted database role to connect as."""


class PartnerRoleNotRestricted(RuntimeError):
    """The partner connection is not the restricted role (it is privileged, or it can read ``users``)."""


# True only for a role with no special attribute that cannot read the users table (what ``freejoy_partner`` is).
RESTRICTED_ROLE_QUERY = text(
    "SELECT NOT (rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb) "
    "AND NOT has_table_privilege(current_user, 'users', 'SELECT') "
    "FROM pg_roles WHERE rolname = current_user"
)


class PartnerSessionProvider:
    """Sessions for the partner surface, on the restricted database role (ADR-013, FREEJ-57).

    Bound to ``PARTNER_DATABASE_URL`` (the role ``freejoy_partner``, created from
    ``commands/sql/partner_role.sql``), never to ``DATABASE_URL``: every partner route and the
    partner authentication use this provider and nothing else, so a bug in partner code cannot
    reach the tables the role cannot see.

    The engine is built on first use, not at import and not in a lifespan hook: a mounted sub-app's
    lifespan never runs, and an engine built at import would need the URL on every deployment that
    leaves the surface off. Fixed to one URL; a missing one fails at the first ``session()``.

    The first session also checks who the connection really is (``RESTRICTED_ROLE_QUERY``): a URL that
    names the owner, or a role that was given too much, is refused with ``PartnerRoleNotRestricted``
    (the surface answers 503) instead of serving as a privileged role. A pass is remembered; a failure is
    checked again on the next session. The message never contains the URL.
    """

    def __init__(self, database_url: str | None) -> None:
        self._database_url = database_url
        self._maker: async_sessionmaker[AsyncSession] | None = None
        self._verified = False

    def _session_maker(self) -> async_sessionmaker[AsyncSession]:
        if self._maker is None:
            if not self._database_url:
                raise PartnerDatabaseUnavailable("PARTNER_DATABASE_URL is not set")
            self._maker = async_sessionmaker(
                build_engine(self._database_url),
                expire_on_commit=settings.EXPIRE_ON_COMMIT,
            )
        return self._maker

    async def _verify_role(self, session: AsyncSession) -> None:
        restricted = (await session.execute(RESTRICTED_ROLE_QUERY)).scalar_one_or_none()
        if restricted is not True:
            raise PartnerRoleNotRestricted(
                "the partner database connection is not the restricted partner role"
            )
        self._verified = True

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self._session_maker()() as session:
            if not self._verified:
                await self._verify_role(session)
            yield session
