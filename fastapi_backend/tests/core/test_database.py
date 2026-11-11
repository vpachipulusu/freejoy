from typing import Any, Self

import pytest
from sqlalchemy import NullPool
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from app.core import database
from app.core.base import Base
from app.core.database import (
    DefaultSessionProvider,
    async_session_maker,
    build_engine,
    engine,
)
from app.core.db_url import (
    CONNECT_TIMEOUT_SECONDS,
    to_async_target,
    unique_prepared_statement_name,
)

POOLER_SAFE_ARGS = {
    "statement_cache_size": 0,
    "prepared_statement_cache_size": 0,
    "prepared_statement_name_func": unique_prepared_statement_name,
    "timeout": CONNECT_TIMEOUT_SECONDS,
}


def test_engine_keeps_null_pool_for_serverless() -> None:
    assert isinstance(engine.pool, NullPool)


def test_default_session_provider_is_the_platform_provider() -> None:
    from app.api.deps import get_session_provider

    assert isinstance(get_session_provider(), DefaultSessionProvider)


def test_base_is_a_declarative_base() -> None:
    assert issubclass(Base, DeclarativeBase)


def test_session_maker_does_not_expire_on_commit_by_default() -> None:
    assert async_session_maker.kw["expire_on_commit"] is False


async def test_default_provider_yields_an_async_session() -> None:
    provider = DefaultSessionProvider(async_session_maker)

    async with provider.session() as session:
        assert isinstance(session, AsyncSession)


async def test_default_provider_uses_the_maker_it_was_given() -> None:
    maker = async_sessionmaker(engine)
    provider = DefaultSessionProvider(maker)

    async with provider.session() as session:
        assert session.bind is engine


def test_build_engine_passes_sslmode_to_asyncpg_and_keeps_null_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    real = database.create_async_engine

    def spy(url: Any, **kwargs: Any) -> AsyncEngine:
        captured.update(kwargs)
        return real(url, **kwargs)

    monkeypatch.setattr(database, "create_async_engine", spy)

    built = build_engine("postgresql://u:s3cret-pw@ep-x.neon.tech/app?sslmode=require")

    assert captured["connect_args"] == {**POOLER_SAFE_ARGS, "ssl": "require"}
    assert captured["poolclass"] is NullPool
    assert isinstance(built.pool, NullPool)
    assert built.url.drivername == "postgresql+asyncpg"
    assert dict(built.url.query) == {}


def test_build_engine_without_tls_parameter_sets_no_ssl_argument(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    real = database.create_async_engine

    def spy(url: Any, **kwargs: Any) -> AsyncEngine:
        captured.update(kwargs)
        return real(url, **kwargs)

    monkeypatch.setattr(database, "create_async_engine", spy)

    build_engine("postgresql://u:pw@localhost:5432/app")

    assert captured["connect_args"] == POOLER_SAFE_ARGS


def test_engine_and_target_reprs_never_contain_the_password() -> None:
    raw = "postgresql://u:s3cret-pw@ep-x.neon.tech/app?sslmode=require"
    built = build_engine(raw)

    assert "s3cret-pw" not in repr(built)
    assert "s3cret-pw" not in str(built.url)
    assert "s3cret-pw" not in repr(to_async_target(raw))


def test_partner_provider_builds_its_engine_on_first_use_from_its_own_url() -> None:
    from app.core.database import PartnerSessionProvider

    url = "postgresql+asyncpg://freejoy_partner:placeholder@localhost:1/partner"
    provider = PartnerSessionProvider(url)
    assert provider._maker is None  # nothing is built at construction

    maker = provider._session_maker()

    assert maker is provider._session_maker()  # built once
    assert maker.kw["bind"] is not engine
    assert maker.kw["bind"].url.username == "freejoy_partner"


class _FakeRoleSession:
    def __init__(self, restricted: object, log: list[str]) -> None:
        self._restricted = restricted
        self._log = log

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def execute(self, statement: object) -> Any:
        self._log.append("verify")
        value = self._restricted

        class Result:
            def scalar_one_or_none(self) -> object:
                return value

        return Result()


def _provider_with(restricted: object, log: list[str]) -> Any:
    from app.core.database import PartnerSessionProvider

    provider = PartnerSessionProvider("postgresql+asyncpg://freejoy_partner:x@h/db")
    provider._maker = lambda: _FakeRoleSession(restricted, log)  # type: ignore[assignment]
    return provider


async def test_partner_provider_checks_the_role_once_and_remembers_a_pass() -> None:
    log: list[str] = []
    provider = _provider_with(True, log)

    for _ in range(3):
        async with provider.session():
            pass

    assert log == ["verify"]


@pytest.mark.parametrize("restricted", [False, None])
async def test_partner_provider_refuses_a_connection_that_is_not_the_restricted_role(
    restricted: object,
) -> None:
    from app.core.database import PartnerRoleNotRestricted

    log: list[str] = []
    provider = _provider_with(restricted, log)

    for _ in range(2):  # a failure is checked again on the next request
        with pytest.raises(PartnerRoleNotRestricted) as caught:
            async with provider.session():
                pass
        assert "freejoy_partner" not in str(caught.value)
        assert "postgresql" not in str(caught.value)

    assert log == ["verify", "verify"]


async def test_partner_provider_without_a_url_fails_when_a_session_is_asked_for() -> (
    None
):
    from app.core.database import PartnerDatabaseUnavailable, PartnerSessionProvider

    provider = PartnerSessionProvider(None)

    with pytest.raises(PartnerDatabaseUnavailable):
        async with provider.session():
            pass
