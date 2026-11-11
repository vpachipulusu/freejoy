"""Run Alembic for the ``*_e2e.py`` fixtures with a time limit and a clear failure (FREEJ-64).

Every fixture that rebuilds a schema goes through :func:`run_alembic`. It exists because a bare
``subprocess.run`` has no limit: when the child waits for a lock that a session of the test run itself holds,
the worker waits for the child, the session waits for the worker, and pytest sits there until someone kills it
(a gate run lost its failure summary that way).

* The child gets ``ALEMBIC_TIMEOUT_SECONDS``; past that it is killed and ``AlembicStalled`` names the command,
  the database and every session still connected to it (state, wait event, how long its transaction has been
  open, its last query).
* Before the child starts, sessions another test left behind on this worker's private database are ended
  (``end_leftover_sessions``): a connection that a failed test never closed holds an ACCESS SHARE lock and
  would block ``DROP TABLE`` for ever. It is reported as a warning, so a leak is visible, not hidden.
* A failing child raises ``AlembicFailed`` carrying the tail of its output; ``CalledProcessError`` alone said
  only "returned non-zero exit status 1".

The database itself also carries ``lock_timeout`` and ``idle_in_transaction_session_timeout`` (``tests/db_guard.py``),
so a lock wait inside the child ends with an error naming the statement.
"""

import asyncio
import os
import subprocess
import sys
import warnings
from collections.abc import Coroutine
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import asyncpg  # type: ignore[import-untyped]
from sqlalchemy.engine import make_url

BACKEND_ROOT = Path(__file__).resolve().parent.parent
ALEMBIC_TIMEOUT_SECONDS = 300
GUARD_APPLICATION_NAME = "freejoy-pytest-guard"
_CONNECT_TIMEOUT_SECONDS = 5
_OUTPUT_TAIL_CHARACTERS = 2000

_SESSIONS_SQL = """
SELECT pid, application_name, state, wait_event_type, wait_event,
       now() - xact_start AS in_transaction, left(query, 120) AS query
FROM pg_stat_activity
WHERE datname = current_database() AND pid <> pg_backend_pid()
ORDER BY pid
"""
_END_SESSIONS_SQL = """
SELECT pid, state, left(query, 80) AS query, pg_terminate_backend(pid) AS ended
FROM pg_stat_activity
WHERE datname = current_database() AND pid <> pg_backend_pid()
  AND application_name <> $1
"""


class AlembicStalled(RuntimeError):
    """Alembic did not finish in time."""


class AlembicFailed(RuntimeError):
    """Alembic exited with an error."""


async def _connect(url: str) -> asyncpg.Connection:
    target = make_url(url)
    return await asyncpg.connect(
        host=target.host,
        port=target.port or 5432,
        user=target.username,
        password=target.password,
        database=target.database,
        timeout=_CONNECT_TIMEOUT_SECONDS,
        server_settings={"application_name": "freejoy-pytest-fixture"},
    )


def _run(coroutine: Coroutine[Any, Any, Any]) -> Any:
    """Run a coroutine to the end on a thread of its own: callers may be inside a running event loop
    (a test that rebuilds the schema halfway, an async fixture), where ``asyncio.run`` refuses to start."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coroutine).result()


async def _fetch(url: str, sql: str, *params: object) -> list[asyncpg.Record]:
    connection = await _connect(url)
    try:
        return await connection.fetch(sql, *params)
    finally:
        await connection.close()


def describe_sessions(url: str) -> str:
    """One line per other session on the database, for a failure message."""
    try:
        rows = _run(_fetch(url, _SESSIONS_SQL))
    except (OSError, TimeoutError, asyncpg.PostgresError) as error:
        return f"(could not list the sessions: {type(error).__name__})"
    if not rows:
        return "(no other session is connected to the database)"
    return "\n".join(
        f"  pid {row['pid']} [{row['application_name'] or '-'}] {row['state']}"
        f" wait={row['wait_event_type']}/{row['wait_event']}"
        f" transaction open for {row['in_transaction']}: {row['query']!r}"
        for row in rows
    )


def end_leftover_sessions(url: str) -> int:
    """End every other session on the database except the run's own guard; the number ended.

    The database is private to this pytest process (``tests/db_guard.py`` fails the run when another process
    holds it), so a session still there when a fixture is about to rebuild the schema was leaked by a test.
    """
    try:
        rows = _run(_fetch(url, _END_SESSIONS_SQL, GUARD_APPLICATION_NAME))
    except (OSError, TimeoutError, asyncpg.PostgresError) as error:
        warnings.warn(
            f"could not check for leftover sessions before the schema rebuild: {type(error).__name__}",
            stacklevel=3,
        )
        return 0
    for row in rows:
        warnings.warn(
            f"ended a session a test left open on the test database (pid {row['pid']}, {row['state']}, "
            f"last query {row['query']!r}); a test did not close its connection",
            stacklevel=3,
        )
    return len(rows)


def run_alembic(url: str, *args: str) -> None:
    """``alembic <args>`` against ``url``; raises ``AlembicStalled`` or ``AlembicFailed`` instead of hanging."""
    database = make_url(url).database
    command = f"alembic {' '.join(args)}"
    end_leftover_sessions(url)
    try:
        finished = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=BACKEND_ROOT,
            env={**os.environ, "DATABASE_URL_DIRECT": url},
            capture_output=True,
            text=True,
            timeout=ALEMBIC_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise AlembicStalled(
            f"`{command}` on database {database} did not finish in {ALEMBIC_TIMEOUT_SECONDS} s and was "
            "killed. Usually it waits for a lock held by another session: a connection a test left open, "
            "or a second pytest run on the same TEST_DATABASE_URL (see docs/developer/testing.md). "
            f"Sessions on the database now:\n{describe_sessions(url)}"
        ) from error
    if finished.returncode != 0:
        tail = (finished.stderr or finished.stdout)[-_OUTPUT_TAIL_CHARACTERS:]
        raise AlembicFailed(
            f"`{command}` on database {database} exited with {finished.returncode}:\n{tail}"
        )
