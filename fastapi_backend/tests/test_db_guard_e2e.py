"""``tests.db_guard`` and ``tests.alembic_support`` against the run's own test database.

Runs only when ``TEST_DATABASE_URL`` names a local ``*_test`` database, like the other ``*_e2e.py`` suites. It uses
the database this process already holds (a pytest-xdist worker's ``<name>_gwN_test``) instead of creating scratch
databases: ``CREATE`` and ``DROP DATABASE`` force a checkpoint, which took minutes on a loaded machine. The schema
is left at head, as the other e2e suites expect to find it or rebuild it.
"""

import asyncio
import os
import warnings
from collections.abc import Iterator

import asyncpg  # type: ignore[import-untyped]
import pytest
from sqlalchemy.engine import make_url

from tests import alembic_support, db_guard
from tests.alembic_support import AlembicStalled, run_alembic
from tests.db_guard import DatabaseInUse, claim_test_database

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not set"
)


@pytest.fixture
def url() -> Iterator[str]:
    assert TEST_DATABASE_URL is not None
    target = make_url(TEST_DATABASE_URL)
    assert (target.database or "").endswith("_test"), (
        "refusing to rebuild this database"
    )
    assert target.host in {"localhost", "127.0.0.1", "::1"}
    yield TEST_DATABASE_URL
    run_alembic(TEST_DATABASE_URL, "upgrade", "head")


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


async def test_the_run_holds_its_database_and_a_second_claim_fails_naming_it(
    url: str,
) -> None:
    with pytest.raises(DatabaseInUse, match=r"already using the test database"):
        await asyncio.to_thread(claim_test_database, url)


async def test_the_run_database_has_the_test_time_limits(url: str) -> None:
    connection = await _connect(url)
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


async def test_a_leaked_session_is_ended_before_a_rebuild_and_an_unreleased_lock_stalls_with_a_name(
    url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    await asyncio.to_thread(run_alembic, url, "upgrade", "head")
    leaked = await _connect(url)
    await leaked.execute("BEGIN")
    # ACCESS SHARE on users, transaction left open: DROP TABLE users would wait for it.
    await leaked.fetch("SELECT count(*) FROM users")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        await asyncio.to_thread(run_alembic, url, "downgrade", "base")

    assert any("left open" in str(item.message) for item in caught)
    with pytest.raises((asyncpg.PostgresError, asyncpg.InterfaceError)):
        await leaked.fetchval("SELECT 1")

    # With the clean-up off, the same lock stalls the child, and the error names the session.
    await asyncio.to_thread(run_alembic, url, "upgrade", "head")
    monkeypatch.setattr(alembic_support, "end_leftover_sessions", lambda url: 0)
    monkeypatch.setattr(alembic_support, "ALEMBIC_TIMEOUT_SECONDS", 6)
    holder = await _connect(url, "the-leaked-session")
    try:
        await holder.execute("BEGIN")
        await holder.fetch("SELECT count(*) FROM users")

        with pytest.raises(AlembicStalled) as stalled:
            await asyncio.to_thread(run_alembic, url, "downgrade", "base")
    finally:
        await holder.close()

    message = str(stalled.value)
    assert "did not finish in 6 s" in message
    assert "the-leaked-session" in message and "idle in transaction" in message


async def test_a_rebuild_started_from_inside_a_running_event_loop_works(
    url: str,
) -> None:
    """``end_leftover_sessions`` is called straight from async tests that rebuild the schema halfway."""
    assert alembic_support.end_leftover_sessions(url) == 0
