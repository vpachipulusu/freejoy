"""``tests.db_guard`` and ``tests.alembic_support`` against the run's own test database.

Runs only when ``TEST_DATABASE_URL`` names a local ``*_test`` database, like the other ``*_e2e.py`` suites. It uses
the database this process already holds (a pytest-xdist worker's ``<name>_gwN_test``) instead of creating scratch
databases: ``CREATE`` and ``DROP DATABASE`` force a checkpoint, which took minutes on a loaded machine. The schema
is left at head, as the other e2e suites expect to find it or rebuild it.
"""

import asyncio
import os
import time
import warnings
from collections.abc import Iterator
from pathlib import Path

import asyncpg  # type: ignore[import-untyped]
import pytest
from sqlalchemy.engine import make_url

from tests import alembic_support, db_guard
from tests.alembic_support import (
    AlembicFailed,
    AlembicStalled,
    LeakedSessionWarning,
    run_alembic,
)
from tests.db_guard import DatabaseInUse, claim_test_database

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not set"
)


@pytest.fixture
def url() -> Iterator[str]:
    assert TEST_DATABASE_URL is not None
    assert db_guard.is_local_test_database(TEST_DATABASE_URL), (
        "refusing to rebuild this database"
    )
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


async def _wait_until_no_leaked_sessions(url: str) -> None:
    """The server drops a killed child's session a moment after the process is gone; wait for that."""
    for _ in range(100):
        if not await asyncio.to_thread(alembic_support.leaked_sessions, url):
            return
        await asyncio.sleep(0.1)


async def test_the_run_holds_its_database_and_a_second_claim_fails_naming_the_holder(
    url: str,
) -> None:
    assert db_guard.holds(make_url(url).database)

    with pytest.raises(
        DatabaseInUse, match=r"already using the test database.*pid \d+"
    ):
        await asyncio.to_thread(claim_test_database, url, strict=True)


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


async def test_a_leaked_session_is_ended_before_a_rebuild_with_a_named_warning(
    url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    report = tmp_path / "leaks.txt"
    monkeypatch.setenv(alembic_support.LEAK_REPORT_ENV, str(report))
    await asyncio.to_thread(run_alembic, url, "upgrade", "head")
    leaked = await _connect(url, "the-leaked-session")
    try:
        await leaked.execute("BEGIN")
        # ACCESS SHARE on users, transaction left open: DROP TABLE users would wait for it.
        await leaked.fetch("SELECT count(*) FROM users")
        await asyncio.sleep(alembic_support._IDLE_GRACE_SECONDS + 0.5)

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            await asyncio.to_thread(run_alembic, url, "downgrade", "base")

        text = [
            str(i.message)
            for i in caught
            if i.category is LeakedSessionWarning
            and "the-leaked-session" in str(i.message)
        ]
        assert len(text) == 1 and "transaction open since" in text[0]
        assert "the-leaked-session" in report.read_text()
        with pytest.raises((asyncpg.PostgresError, asyncpg.InterfaceError)):
            await leaked.fetchval("SELECT 1")
    finally:
        leaked.terminate()


async def test_a_session_that_is_fresh_or_the_guards_own_is_not_ended(url: str) -> None:
    fresh = await _connect(url, "a-test-connection")
    try:
        # Idle and just opened: inside the grace period, so not a leak yet.
        assert await asyncio.to_thread(alembic_support.end_leftover_sessions, url) == 0
        assert await fresh.fetchval("SELECT 1") == 1
    finally:
        await fresh.close()
    assert db_guard.holds(make_url(url).database)  # the guard connection survived too


async def test_an_unreleased_lock_stalls_with_the_session_named_and_the_child_gone(
    url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    await asyncio.to_thread(run_alembic, url, "upgrade", "head")
    monkeypatch.setattr(alembic_support, "end_leftover_sessions", lambda url: 0)
    monkeypatch.setattr(alembic_support, "ALEMBIC_TIMEOUT_SECONDS", 2)
    holder = await _connect(url, "the-holder-session")
    try:
        await holder.execute("BEGIN")
        await holder.fetch("SELECT count(*) FROM users")

        with pytest.raises(AlembicStalled) as stalled:
            await asyncio.to_thread(run_alembic, url, "downgrade", "base")
    finally:
        await holder.close()
        await _wait_until_no_leaked_sessions(url)

    message = str(stalled.value)
    assert "did not finish in 2 s" in message
    assert "the-holder-session" in message and "idle in transaction" in message


async def test_a_lock_timeout_in_the_child_fails_with_the_sessions_listed(
    url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real ordering: the database's lock_timeout ends the wait long before the child's limit."""
    await asyncio.to_thread(run_alembic, url, "upgrade", "head")
    target = make_url(url)
    quoted = (target.database or "").replace('"', '""')
    admin = await _connect(url, "the-test-admin")
    holder = await _connect(url, "the-lock-holder")
    try:
        await admin.execute(f"ALTER DATABASE \"{quoted}\" SET lock_timeout = '1s'")
        await holder.execute("BEGIN")
        await holder.fetch("SELECT count(*) FROM users")
        monkeypatch.setattr(alembic_support, "end_leftover_sessions", lambda url: 0)
        with pytest.raises(AlembicFailed) as failed:
            await asyncio.to_thread(run_alembic, url, "downgrade", "base")
    finally:
        await holder.close()
        await admin.execute(
            f"ALTER DATABASE \"{quoted}\" SET lock_timeout = '{db_guard.LOCK_TIMEOUT}'"
        )
        await admin.close()
        await _wait_until_no_leaked_sessions(url)

    message = str(failed.value)
    assert "lock timeout" in message.lower()
    assert "Sessions on the database now:" in message and "the-lock-holder" in message


async def test_a_rebuild_started_from_inside_a_running_event_loop_works(
    url: str,
) -> None:
    """``end_leftover_sessions`` is called straight from async tests that rebuild the schema halfway."""
    started = time.monotonic()
    ended = alembic_support.end_leftover_sessions(url)

    assert isinstance(ended, int) and ended >= 0
    assert time.monotonic() - started < alembic_support._CHECK_DEADLINE_SECONDS + 10
