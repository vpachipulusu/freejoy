"""Claim the test database for this pytest process and give it test-sized time limits (FREEJ-64).

Called from ``tests/conftest.py`` once the database a process will use is known (a pytest-xdist worker's
``<name>_gwN_test``, or ``TEST_DATABASE_URL`` itself for a plain run and for the xdist controller).

* **One pytest process per database.** The process takes a session-level advisory lock on the database and
  keeps it until it exits. A second process that picks the same database (two runs from one checkout, or the
  gate and a manual run, both name workers ``gw0``, ``gw1``, ...) fails at start with a message instead of
  rebuilding the schema under the first run's feet, which showed up as ``ProgrammingError`` in unrelated tests.
* **Time limits that turn a stall into an error.** ``ALTER DATABASE ... SET`` gives every new session on the
  database ``lock_timeout`` (a lock wait ends with an error that names the statement),
  ``idle_in_transaction_session_timeout`` (a connection a test left inside a transaction is ended by the server
  instead of blocking ``DROP TABLE`` for ever) and ``statement_timeout``. Tests that need a shorter limit set
  their own with ``SET LOCAL``.

The lock lives on a connection owned by a daemon thread with its own event loop, so it outlives the short-lived
``asyncio.run`` calls the fixtures make.
"""

import asyncio
import sys
import threading
import warnings
from concurrent.futures import TimeoutError as FutureTimeoutError

import asyncpg  # type: ignore[import-untyped]
from sqlalchemy.engine import make_url

from tests._worker_db import LOCAL_HOSTS
from tests.alembic_support import GUARD_APPLICATION_NAME

ADVISORY_LOCK_KEY = (
    0x46524A36  # "FRJ6": one key for the whole suite, scoped by database
)
LOCK_TIMEOUT = "60s"
IDLE_IN_TRANSACTION_TIMEOUT = "180s"
STATEMENT_TIMEOUT = "240s"
_CONNECT_TIMEOUT_SECONDS = 10
_CLAIM_DEADLINE_SECONDS = 30

# Kept alive for the life of the process: dropping the connection would drop the lock.
_holder: dict[str, object] = {}


class DatabaseInUse(RuntimeError):
    """Another pytest process already holds this test database."""


async def _claim(url: str) -> asyncpg.Connection:
    target = make_url(url)
    connection = await asyncpg.connect(
        host=target.host,
        port=target.port or 5432,
        user=target.username,
        password=target.password,
        database=target.database,
        timeout=_CONNECT_TIMEOUT_SECONDS,
        server_settings={"application_name": GUARD_APPLICATION_NAME},
    )
    if not await connection.fetchval(
        "SELECT pg_try_advisory_lock($1)", ADVISORY_LOCK_KEY
    ):
        await connection.close()
        raise DatabaseInUse(
            f"another pytest process is already using the test database {target.database}. Two runs that "
            "share one TEST_DATABASE_URL rebuild each other's schema; run one at a time, or give each run "
            "its own database name (docs/developer/testing.md, Parallel runs)."
        )
    quoted = (target.database or "").replace('"', '""')
    try:
        for setting, value in (
            ("lock_timeout", LOCK_TIMEOUT),
            ("idle_in_transaction_session_timeout", IDLE_IN_TRANSACTION_TIMEOUT),
            ("statement_timeout", STATEMENT_TIMEOUT),
        ):
            await connection.execute(
                f"ALTER DATABASE \"{quoted}\" SET {setting} = '{value}'"
            )
    except asyncpg.PostgresError as error:
        warnings.warn(
            f"could not set test time limits on {target.database}: {type(error).__name__}"
        )
    return connection


def claim_test_database(url: str) -> None:
    """Take the lock and set the limits, or raise ``DatabaseInUse``. Skips what the tests would refuse anyway.

    A database that is not a local ``*_test`` one is left alone (the ``*_e2e.py`` suites refuse it), and so is
    one the server cannot be reached for: the tests report that themselves, with their own message.
    """
    target = make_url(url)
    if not (target.database or "").endswith("_test") or target.host not in LOCAL_HOSTS:
        return
    loop = asyncio.new_event_loop()
    threading.Thread(
        target=loop.run_forever, name="freejoy-test-db-guard", daemon=True
    ).start()
    future = asyncio.run_coroutine_threadsafe(_claim(url), loop)
    try:
        connection = future.result(timeout=_CLAIM_DEADLINE_SECONDS)
    except DatabaseInUse:
        loop.call_soon_threadsafe(loop.stop)
        raise
    except (OSError, TimeoutError, FutureTimeoutError, asyncpg.PostgresError) as error:
        print(
            f"test database guard skipped ({type(error).__name__}): the tests that need the database will say why",
            file=sys.stderr,
        )
        loop.call_soon_threadsafe(loop.stop)
        return
    _holder.update(connection=connection, loop=loop)


def release_test_database() -> None:
    """Drop the lock and stop the guard thread (for the tests of this module; a run keeps it until it exits)."""
    connection = _holder.pop("connection", None)
    loop = _holder.pop("loop", None)
    if not isinstance(loop, asyncio.AbstractEventLoop):
        return
    if isinstance(connection, asyncpg.Connection):
        asyncio.run_coroutine_threadsafe(connection.close(), loop).result(timeout=10)
    loop.call_soon_threadsafe(loop.stop)
