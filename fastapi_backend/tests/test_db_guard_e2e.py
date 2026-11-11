"""``tests.db_guard`` and ``tests.alembic_support`` against a real Postgres (a throwaway database per test).

Runs only when ``TEST_DATABASE_URL`` names a local ``*_test`` database, like the other ``*_e2e.py`` suites.
"""

import asyncio
import warnings
from collections.abc import AsyncIterator

import asyncpg  # type: ignore[import-untyped]
import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.database import build_engine
from tests import alembic_support, db_guard
from tests.alembic_support import AlembicStalled, run_alembic
from tests.db_guard import DatabaseInUse, claim_test_database, release_test_database
from tests.pg_scaffold import (
    TEST_DATABASE_URL,
    create_database,
    drop_databases,
    unique_name,
    url_for,
)

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not set"
)


@pytest.fixture
async def server() -> AsyncIterator[AsyncEngine]:
    engine = build_engine(url_for(make_url(TEST_DATABASE_URL or "").database or ""))
    yield engine
    await engine.dispose()


@pytest.fixture
async def scratch_url(server: AsyncEngine) -> AsyncIterator[str]:
    name = unique_name("guard", "test")
    await create_database(server, name)
    yield url_for(name)
    release_test_database()
    await drop_databases(server, [name])


async def _connect(
    url: str, application_name: str = "test-holder"
) -> asyncpg.Connection:
    target = make_url(url)
    return await asyncpg.connect(
        host=target.host,
        port=target.port or 5432,
        user=target.username,
        password=target.password,
        database=target.database,
        timeout=10,
        server_settings={"application_name": application_name},
    )


@pytest.fixture
async def migrated_url(scratch_url: str) -> str:
    await asyncio.to_thread(run_alembic, scratch_url, "upgrade", "head")
    return scratch_url


async def test_a_second_claim_of_the_same_database_fails_naming_it(
    scratch_url: str,
) -> None:
    await asyncio.to_thread(claim_test_database, scratch_url)

    with pytest.raises(DatabaseInUse, match=r"already using the test database guard_"):
        await asyncio.to_thread(claim_test_database, scratch_url)


async def test_a_claimed_database_gets_the_test_time_limits(scratch_url: str) -> None:
    await asyncio.to_thread(claim_test_database, scratch_url)

    connection = await _connect(scratch_url)
    try:
        shown = {
            name: await connection.fetchval(f"SHOW {name}")
            for name in (
                "lock_timeout",
                "idle_in_transaction_session_timeout",
                "statement_timeout",
            )
        }
    finally:
        await connection.close()

    assert shown == {
        "lock_timeout": "1min",
        "idle_in_transaction_session_timeout": "3min",
        "statement_timeout": "4min",
    }
    assert db_guard.LOCK_TIMEOUT == "60s"


async def test_a_session_a_test_left_open_is_ended_before_the_schema_rebuild(
    migrated_url: str,
) -> None:
    leaked = await _connect(migrated_url)
    await leaked.execute("BEGIN")
    # ACCESS SHARE on users, transaction left open: DROP TABLE users would wait for it.
    await leaked.fetch("SELECT count(*) FROM users")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        await asyncio.to_thread(run_alembic, migrated_url, "downgrade", "base")

    assert any("left open" in str(item.message) for item in caught)
    with pytest.raises((asyncpg.PostgresError, asyncpg.InterfaceError)):
        await leaked.fetchval("SELECT 1")


async def test_a_lock_that_is_not_released_stalls_with_the_sessions_named_not_for_ever(
    migrated_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(alembic_support, "end_leftover_sessions", lambda url: 0)
    monkeypatch.setattr(alembic_support, "ALEMBIC_TIMEOUT_SECONDS", 6)
    holder = await _connect(migrated_url, "the-leaked-session")
    try:
        await holder.execute("BEGIN")
        await holder.fetch("SELECT count(*) FROM users")

        with pytest.raises(AlembicStalled) as stalled:
            await asyncio.to_thread(run_alembic, migrated_url, "downgrade", "base")
    finally:
        await holder.close()

    message = str(stalled.value)
    assert "did not finish in 6 s" in message
    assert "the-leaked-session" in message and "idle in transaction" in message
