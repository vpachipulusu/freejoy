"""Run Alembic for the ``*_e2e.py`` fixtures with a time limit and a clear failure (FREEJ-64).

Every fixture that rebuilds a schema goes through :func:`run_alembic`. A bare ``subprocess.run`` has no limit and
reported a failing child only as "returned non-zero exit status 1".

* The child gets ``ALEMBIC_TIMEOUT_SECONDS``. Shortly before that it dumps the stack of its own Python threads
  (``faulthandler``, same idea as the hard per-test limit in ``tests/conftest.py``) and exits, so a stall shows
  where the child was; past the limit the sessions on the database are listed first, then the child is killed.
  ``AlembicStalled`` carries the dump and the sessions (state, wait event, ``pg_blocking_pids``, locks held and
  waited for, transaction age, last query).
* A child that fails (for example on ``lock_timeout``) raises ``AlembicFailed`` with the end of its output, and
  the session list is appended when the output mentions a timeout.
* Before the child starts, client sessions another test left behind on this process's own database are ended
  (``end_leftover_sessions``) when this process holds the database guard (``tests/db_guard.py``): a connection a
  failed test never closed keeps its locks and makes ``DROP TABLE`` wait. Every one ended is reported as a
  ``LeakedSessionWarning`` and counted in the file named by ``FREEJOY_LEAK_REPORT_FILE`` (the gate prints it).

What this does NOT explain: the stall of 2026-11-11, where two workers stood still with sessions that were idle
(waiting for their client), not waiting for a lock, and ending those sessions did not release the workers. That
needs the hard per-test limit and the stack it prints.

Every database call here has a client-side time limit, so the diagnostics cannot become a second hang.
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

from tests import db_guard
from tests.db_guard import GUARD_APPLICATION_NAME

BACKEND_ROOT = Path(__file__).resolve().parent.parent
ALEMBIC_TIMEOUT_SECONDS = 300
LEAK_REPORT_ENV = "FREEJOY_LEAK_REPORT_FILE"
_OUTPUT_TAIL_CHARACTERS = 6000
_CONNECT_TIMEOUT_SECONDS = 5
_QUERY_TIMEOUT_SECONDS = 10
_CLOSE_TIMEOUT_SECONDS = 5
_CHECK_DEADLINE_SECONDS = 30
_KILL_READ_TIMEOUT_SECONDS = 10
_IDLE_GRACE_SECONDS = 2
_TERMINATE_WAIT_MILLISECONDS = 5000
_FIXTURE_APPLICATION_NAME = "freejoy-pytest-fixture"

# The child is `alembic <args>` (the same entry point as `python -m alembic`) behind a faulthandler timer.
_ALEMBIC_ENTRY = (
    'from alembic.config import main; main(argv=sys.argv[1:], prog="alembic")'
)

_SESSION_REPORT_SQL = """
SELECT a.pid, a.application_name, a.state, a.wait_event_type, a.wait_event,
       pg_blocking_pids(a.pid) AS blocked_by, a.backend_start, a.xact_start, a.state_change,
       now() - a.xact_start AS in_transaction,
       (SELECT left(string_agg(l.locktype || ':' || l.mode || CASE WHEN l.granted THEN '' ELSE ' (waiting)' END,
                               ', '), 300)
          FROM pg_locks l WHERE l.pid = a.pid) AS locks,
       left(a.query, 200) AS query
FROM pg_stat_activity a
WHERE a.datname = current_database() AND a.pid <> pg_backend_pid() AND a.backend_type = 'client backend'
ORDER BY a.backend_start
LIMIT 50
"""
# Client sessions of other tests, never this query's own and never the guard: busy ones, and idle ones that
# have been idle longer than a moment (a connection closing right now is not a leak).
_LEFTOVER_WHERE = f"""
FROM pg_stat_activity
WHERE datname = current_database() AND pid <> pg_backend_pid() AND backend_type = 'client backend'
  AND application_name <> $1
  AND (state <> 'idle' OR state_change < clock_timestamp() - interval '{_IDLE_GRACE_SECONDS} seconds')
"""
_END_LEFTOVER_SQL = (
    "SELECT pid, application_name, state, xact_start, left(query, 80) AS query, "
    f"pg_terminate_backend(pid, {_TERMINATE_WAIT_MILLISECONDS}) AS ended {_LEFTOVER_WHERE}"
)
# A session that can block DDL: inside a transaction, or running for a while.
_LEAKED_SQL = """
SELECT pid, application_name, state, xact_start, left(query, 80) AS query
FROM pg_stat_activity
WHERE datname = current_database() AND pid <> pg_backend_pid() AND backend_type = 'client backend'
  AND application_name <> $1
  AND (state LIKE 'idle in transaction%'
       OR (state = 'active' AND state_change < clock_timestamp() - interval '5 seconds'))
"""


class LeakedSessionWarning(UserWarning):
    """A test left a database session open (and it was ended, or it is reported)."""


class AlembicStalled(RuntimeError):
    """Alembic did not finish in time."""


class AlembicFailed(RuntimeError):
    """Alembic exited with an error."""


def _run(coroutine: Coroutine[Any, Any, Any]) -> Any:
    """Run a coroutine to the end on a thread of its own, within ``_CHECK_DEADLINE_SECONDS``.

    Callers may be inside a running event loop (an async test rebuilding the schema halfway), where
    ``asyncio.run`` refuses to start, and nothing here may wait for ever.
    """

    async def bounded() -> Any:
        return await asyncio.wait_for(coroutine, _CHECK_DEADLINE_SECONDS)

    pool = ThreadPoolExecutor(max_workers=1)
    try:
        return pool.submit(asyncio.run, bounded()).result(
            timeout=_CHECK_DEADLINE_SECONDS + 10
        )
    finally:
        pool.shutdown(wait=False)


async def _fetch(url: str, sql: str, *params: object) -> list[asyncpg.Record]:
    target = make_url(url)
    connection = await asyncpg.connect(
        host=target.host,
        port=target.port or 5432,
        user=target.username,
        password=target.password,
        database=target.database,
        timeout=_CONNECT_TIMEOUT_SECONDS,
        server_settings={"application_name": _FIXTURE_APPLICATION_NAME},
    )
    try:
        return await connection.fetch(sql, *params, timeout=_QUERY_TIMEOUT_SECONDS)
    finally:
        try:
            await asyncio.wait_for(connection.close(), _CLOSE_TIMEOUT_SECONDS)
        except Exception:  # noqa: BLE001  (a close that hangs or fails must not hide the result: drop the socket)
            connection.terminate()


def _clock(value: Any) -> str:
    return f"{value:%H:%M:%S}" if value is not None else "-"


def _session_line(row: asyncpg.Record) -> str:
    return (
        f"  pid {row['pid']} [{row['application_name'] or '-'}] {row['state']}"
        f" wait={row['wait_event_type']}/{row['wait_event']} blocked_by={list(row['blocked_by'])}"
        f" backend_start={_clock(row['backend_start'])} xact_start={_clock(row['xact_start'])}"
        f" in_transaction={row['in_transaction']} state_change={_clock(row['state_change'])}"
        f" locks=[{row['locks'] or ''}] query={row['query']!r}"
    )


def describe_sessions(url: str) -> str:
    """One line per client session on the database, for a failure message. Never raises, never hangs."""
    try:
        rows = _run(_fetch(url, _SESSION_REPORT_SQL))
    except Exception as error:  # noqa: BLE001
        return f"(could not list the sessions: {type(error).__name__})"
    if not rows:
        return "(no other client session is connected to the database)"
    return "\n".join(_session_line(row) for row in rows)


def _describe_leak(row: asyncpg.Record) -> str:
    return (
        f"pid {row['pid']} [{row['application_name'] or '-'}] {row['state']}, "
        f"transaction open since {_clock(row['xact_start'])}, last query {row['query']!r}"
    )


def leaked_sessions(url: str) -> list[str]:
    """Descriptions of sessions on the database that can block DDL, without touching them."""
    try:
        rows = _run(_fetch(url, _LEAKED_SQL, GUARD_APPLICATION_NAME))
    except Exception as error:  # noqa: BLE001
        warnings.warn(
            f"could not check for leaked sessions: {type(error).__name__}",
            stacklevel=2,
        )
        return []
    return [_describe_leak(row) for row in rows]


def end_leftover_sessions(url: str) -> int:
    """End client sessions of other tests on this process's database; the number ended.

    Only when the URL is a local ``*_test`` database AND this process holds its guard (``tests/db_guard.py``),
    so another run's sessions can never be touched. Each session ended is a ``LeakedSessionWarning``.
    """
    database = make_url(url).database
    if not db_guard.is_local_test_database(url):
        raise ValueError("refusing to end sessions outside a local *_test database")
    if not db_guard.holds(database):
        warnings.warn(
            f"not ending leftover sessions on {database}: this process does not hold its guard",
            stacklevel=2,
        )
        return 0
    try:
        rows = _run(_fetch(url, _END_LEFTOVER_SQL, GUARD_APPLICATION_NAME))
    except Exception as error:  # noqa: BLE001
        warnings.warn(
            f"could not check for leftover sessions before the schema rebuild: {type(error).__name__}",
            stacklevel=2,
        )
        return 0
    ended = [row for row in rows if row["ended"]]
    for row in ended:
        text = _describe_leak(row)
        warnings.warn(
            f"ended a session a test left open on {database}: {text}",
            LeakedSessionWarning,
            stacklevel=2,
        )
        _record_leak(text)
    return len(ended)


def _record_leak(text: str) -> None:
    path = os.environ.get(LEAK_REPORT_ENV)
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as report:
            report.write(text + "\n")
    except OSError:
        pass  # the report is a courtesy for the gate; the warning already carries the fact


def _child_command(args: tuple[str, ...], limit: float) -> list[str]:
    """``alembic <args>``, dumping its own stack shortly before ``limit`` and then exiting."""
    dump_after = max(limit - 20, limit * 0.9)
    prologue = (
        "import faulthandler, sys; "
        f"faulthandler.dump_traceback_later({dump_after}, exit=True); "
    )
    return [sys.executable, "-c", prologue + _ALEMBIC_ENTRY, *args]


def _tail(text: str) -> str:
    return text[-_OUTPUT_TAIL_CHARACTERS:]


def _stalled_message(
    command: str, database: str | None, limit: float, output: str, sessions: str
) -> str:
    return (
        f"`{command}` on database {database} did not finish in {limit:g} s and was ended. It is waiting for "
        "something: a lock held by another session (a connection a test left open, or a second pytest run "
        "on the same TEST_DATABASE_URL, see docs/developer/testing.md) or for the server.\n"
        f"Sessions on the database:\n{sessions}\n"
        f"The child's output (its Python stack when it dumped one):\n{_tail(output)}"
    )


def run_alembic(url: str, *args: str) -> None:
    """``alembic <args>`` against ``url``; raises ``AlembicStalled`` or ``AlembicFailed`` instead of hanging."""
    if not db_guard.is_local_test_database(url):
        raise ValueError("refusing to run alembic outside a local *_test database")
    database = make_url(url).database
    command = f"alembic {' '.join(args)}"
    limit = ALEMBIC_TIMEOUT_SECONDS
    end_leftover_sessions(url)
    process = subprocess.Popen(
        _child_command(args, limit),
        cwd=BACKEND_ROOT,
        env={**os.environ, "DATABASE_URL_DIRECT": url},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
    )
    try:
        stdout, stderr = process.communicate(timeout=limit)
    except subprocess.TimeoutExpired:
        sessions = describe_sessions(
            url
        )  # before the kill: the child's own session is still there
        process.kill()
        try:
            stdout, stderr = process.communicate(timeout=_KILL_READ_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", "(the child's output could not be read after the kill)"
        raise AlembicStalled(
            _stalled_message(command, database, limit, stderr or stdout, sessions)
        ) from None
    if process.returncode == 0:
        return
    output = stderr or stdout
    if (
        "Timeout (0:" in output
    ):  # the child's own faulthandler timer fired just before the limit
        raise AlembicStalled(
            _stalled_message(command, database, limit, output, describe_sessions(url))
        )
    message = f"`{command}` on database {database} exited with {process.returncode}:\n{_tail(output)}"
    if "timeout" in output.lower():
        message += f"\nSessions on the database now:\n{describe_sessions(url)}"
    raise AlembicFailed(message)
