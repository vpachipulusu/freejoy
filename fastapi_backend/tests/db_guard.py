"""Claim the test database for this pytest process and give it test-sized time limits (FREEJ-64).

Called from ``tests/conftest.py`` once the database a process will use is known (a pytest-xdist worker's
``<name>_gwN_test``, or ``TEST_DATABASE_URL`` itself for a plain run and for the xdist controller). It runs at
conftest import, so an exported ``TEST_DATABASE_URL`` also refuses a second run that selects only tests that
need no database.

* **One pytest process per database.** The process takes a session-level advisory lock on the database and
  keeps it until it exits. A second process that picks the same database (two runs from one checkout, or the
  gate and a manual run; both name workers ``gw0``, ``gw1``, ...) fails at start with a message naming the
  holder instead of rebuilding the schema under the first run's feet, which showed up as ``ProgrammingError``
  in unrelated tests.
* **Time limits that turn a lock wait into an error.** ``ALTER DATABASE ... SET`` gives every new session on the
  database ``lock_timeout`` (a lock wait ends with an error that names the statement),
  ``idle_in_transaction_session_timeout`` and ``statement_timeout``. They persist on the database until reset
  (``docs/developer/testing.md``). They do NOT explain or end the stall of 2026-11-11: the two stuck workers
  held sessions that were idle (``ClientRead``), not waiting for a lock, and ending those sessions did not
  unblock the workers. A stuck worker is ended by the hard per-test limit in ``tests/conftest.py``.

The lock lives on a connection owned by a daemon thread with its own event loop, so it outlives the short-lived
``asyncio.run`` calls the fixtures make.

The guard fails closed: in a worker, and for any database error, the run stops with a message. Only an
unreachable server in a plain run is left to the tests that need the database, which report it themselves.
"""

import asyncio
import sys
import threading
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any

import asyncpg  # type: ignore[import-untyped]
from sqlalchemy.engine import make_url

from tests._worker_db import LOCAL_HOSTS

GUARD_APPLICATION_NAME = "freejoy-pytest-guard"
# Above the int4 range, so it can never equal a hashtext() value; in line with the app's own keys (7460046001...).
ADVISORY_LOCK_KEY = 7_460_046_064
LOCK_TIMEOUT = "60s"
IDLE_IN_TRANSACTION_TIMEOUT = "180s"
STATEMENT_TIMEOUT = "240s"
_CONNECT_TIMEOUT_SECONDS = 10
_QUERY_TIMEOUT_SECONDS = 15
_CLAIM_DEADLINE_SECONDS = 30

_HOLDER_SQL = """
SELECT l.pid, a.backend_start
FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid
WHERE l.locktype = 'advisory' AND l.classid::bigint = ($1::bigint >> 32)
  AND l.objid::bigint = ($1::bigint & 4294967295) AND l.granted
LIMIT 1
"""

# Kept alive for the life of the process: dropping a connection would drop its lock. Keyed by database name.
_held: dict[str, tuple[Any, asyncio.AbstractEventLoop]] = {}


class DatabaseInUse(RuntimeError):
    """Another pytest process already holds this test database."""


class DatabaseGuardError(RuntimeError):
    """The guard could not be set up; the run must not go on without it."""


def is_local_test_database(url: str) -> bool:
    target = make_url(url)
    return (target.database or "").endswith("_test") and target.host in LOCAL_HOSTS


def holds(database: str | None) -> bool:
    """Whether this process holds the guard of ``database`` (only then may it end other sessions on it)."""
    return database in _held


async def _connect(url: str) -> asyncpg.Connection:
    target = make_url(url)
    return await asyncpg.connect(
        host=target.host,
        port=target.port or 5432,
        user=target.username,
        password=target.password,
        database=target.database,
        timeout=_CONNECT_TIMEOUT_SECONDS,
        server_settings={"application_name": GUARD_APPLICATION_NAME},
    )


async def _holder_description(connection: asyncpg.Connection) -> str:
    try:
        row = await connection.fetchrow(
            _HOLDER_SQL, ADVISORY_LOCK_KEY, timeout=_QUERY_TIMEOUT_SECONDS
        )
    except asyncpg.PostgresError:
        return "its pid could not be read"
    if row is None:
        return "its pid could not be read"
    return f"pid {row['pid']}, connected since {row['backend_start']:%H:%M:%S}"


async def _claim(url: str) -> asyncpg.Connection:
    target = make_url(url)
    connection = await _connect(url)
    try:
        if not await connection.fetchval(
            "SELECT pg_try_advisory_lock($1)",
            ADVISORY_LOCK_KEY,
            timeout=_QUERY_TIMEOUT_SECONDS,
        ):
            holder = await _holder_description(connection)
            raise DatabaseInUse(
                f"another pytest process is already using the test database {target.database} ({holder}). "
                "Two runs that share one TEST_DATABASE_URL rebuild each other's schema; run one at a "
                "time, or give each run its own database name (docs/developer/testing.md)."
            )
        quoted = (target.database or "").replace('"', '""')
        for setting, value in (
            ("lock_timeout", LOCK_TIMEOUT),
            ("idle_in_transaction_session_timeout", IDLE_IN_TRANSACTION_TIMEOUT),
            ("statement_timeout", STATEMENT_TIMEOUT),
        ):
            await connection.execute(
                f"ALTER DATABASE \"{quoted}\" SET {setting} = '{value}'",
                timeout=_QUERY_TIMEOUT_SECONDS,
            )
    except BaseException:
        connection.terminate()
        raise
    return connection


def _hold(
    url: str, outcome: "Future[asyncpg.Connection]", abandoned: threading.Event
) -> None:
    """Thread target: claim on this thread's own loop, then keep the loop (and the lock) alive."""
    loop = asyncio.new_event_loop()
    try:
        connection = loop.run_until_complete(_claim(url))
    except BaseException as error:  # noqa: BLE001  (handed to the caller, which re-raises it)
        loop.close()
        outcome.set_exception(error)
        return
    if (
        abandoned.is_set()
    ):  # the caller gave up on the deadline: do not keep a lock nobody knows about
        connection.terminate()
        loop.close()
        return
    _held[make_url(url).database or ""] = (connection, loop)
    outcome.set_result(connection)
    loop.run_forever()


def claim_test_database(url: str, *, strict: bool) -> bool:
    """Take the lock and set the limits. True when claimed; False when skipped.

    Raises ``DatabaseInUse`` when another process holds the database and ``DatabaseGuardError`` for any other
    failure. A database that is not a local ``*_test`` one is skipped (the ``*_e2e.py`` suites refuse it). With
    ``strict`` False (a plain run or the xdist controller), an unreachable server is skipped with a note on
    stderr: the tests that need the database report it. A worker is always strict.
    """
    if not is_local_test_database(url):
        return False
    outcome: Future[asyncpg.Connection] = Future()
    abandoned = threading.Event()
    threading.Thread(
        target=_hold,
        args=(url, outcome, abandoned),
        name="freejoy-test-db-guard",
        daemon=True,
    ).start()
    try:
        outcome.result(timeout=_CLAIM_DEADLINE_SECONDS)
    except DatabaseInUse:
        raise
    except (OSError, TimeoutError, FutureTimeoutError) as error:
        abandoned.set()
        if strict:
            raise DatabaseGuardError(
                f"cannot reach the test database to claim it ({type(error).__name__})"
            ) from error
        print(
            f"test database guard skipped ({type(error).__name__}): the tests that need the database will say why",
            file=sys.stderr,
        )
        return False
    except Exception as error:
        raise DatabaseGuardError(
            f"cannot claim the test database ({type(error).__name__}: {error})"
        ) from error
    return True
