import faulthandler
import os
from collections.abc import Iterator
from typing import TextIO

import pytest

from app.core.settings_environment import isolate_settings_environment

# app.core.config builds its settings when it is first imported, so the machine's own
# values (a git-ignored .env, exported APP_NAME, a real SENTRY_DSN, ...) have to go before
# any test module or fixture imports the app. Child processes inherit the clean environment.
isolate_settings_environment(os.environ)

# The longest one test may run, fixtures included. Above two Alembic limits (tests/alembic_support.py: 300 s each,
# a module fixture can run two). A test still running then has its stack, and every thread's, written to stderr
# and the process ends at once (`faulthandler`): the run reports the worker as crashed on that test instead of
# waiting for ever (FREEJ-64). The environment variable is only for the tests of this hook.
HARD_TEST_LIMIT_SECONDS = float(
    os.environ.get("FREEJOY_HARD_TEST_LIMIT_SECONDS", "900")
)


_real_stderr: list[TextIO] = []


def _stderr_that_survives_capture(config: pytest.Config) -> TextIO:
    """The process's real stderr, as pytest's own faulthandler plugin keeps it for the same purpose.

    pytest captures file descriptor 2 during a test (and a pytest-xdist worker's console is not what a fresh
    ``os.dup(2)`` gives back), so a dump written to descriptor 2 just before the process ends would land in a
    temporary file nobody reads. pytest's faulthandler plugin saves a copy of the real descriptor at start-up
    (``fault_handler_stderr_fd_key``); that copy is used here, falling back to one taken with capture off.
    """
    if not _real_stderr:
        from _pytest.faulthandler import fault_handler_stderr_fd_key

        saved = config.stash.get(fault_handler_stderr_fd_key, None)
        if saved is None:
            manager = config.pluginmanager.getplugin("capturemanager")
            with manager.global_and_fixture_disabled():
                saved = os.dup(2)
        _real_stderr.append(os.fdopen(os.dup(saved), "w"))
    return _real_stderr[0]


@pytest.hookimpl(wrapper=True, trylast=True)
def pytest_runtest_protocol(item: pytest.Item) -> Iterator[None]:
    faulthandler.dump_traceback_later(
        HARD_TEST_LIMIT_SECONDS,
        exit=True,
        file=_stderr_that_survives_capture(item.config),
    )
    try:
        return (yield)
    finally:
        faulthandler.cancel_dump_traceback_later()


@pytest.fixture(scope="module", autouse=True)
def _no_leaked_database_sessions(request: pytest.FixtureRequest) -> Iterator[None]:
    """Fail the module that leaves a session open in a transaction on the run's database.

    Such a session keeps its locks and makes the next module's schema rebuild wait. ``run_alembic`` ends it as
    a backstop; this names the module that leaked it. Runs only where this process holds the database guard.
    """
    yield
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        return
    from sqlalchemy.engine import make_url

    from tests import db_guard

    if not db_guard.holds(make_url(url).database):
        return
    from tests.alembic_support import leaked_sessions

    leaked = leaked_sessions(url)
    if leaked:
        pytest.fail(
            f"{request.module.__name__} left {len(leaked)} database session(s) open:\n  "
            + "\n  ".join(leaked),
            pytrace=False,
        )


def _claim_database(url: str, *, strict: bool) -> None:
    """Lock the database to this process and set its time limits; stop the run if that cannot be done.

    A second pytest process on the same database, or any database error, ends the run with a message.
    Only an unreachable server in a plain run (``strict`` False) is left to the tests that need it.
    """
    from tests.db_guard import DatabaseGuardError, DatabaseInUse, claim_test_database

    try:
        claim_test_database(url, strict=strict)
    except (DatabaseInUse, DatabaseGuardError) as error:
        pytest.exit(str(error), 4)


def _isolate_worker_database() -> None:
    """Give each pytest-xdist worker its own database.

    The real-route tests rebuild the schema of ``TEST_DATABASE_URL`` (alembic
    ``downgrade base`` then ``upgrade head``) and truncate tables between tests,
    so two workers must never share one database. A worker ``gw3`` running
    against ``yourapp_test`` uses ``yourapp_gw3_test`` instead (see
    ``tests/_worker_db.py``), created on first use, migrated to head and rebuilt by
    the tests themselves. Without xdist a plain run (and the xdist controller) uses
    ``TEST_DATABASE_URL`` itself; without a test database this does nothing. Either way the
    process claims its database (``tests/db_guard.py``: an advisory lock, so a second run on the
    same database fails at start, and test-sized ``lock_timeout`` and friends). Any failure stops
    the run with a clear message rather than letting workers share a database.

    It runs at import time because the test modules read ``TEST_DATABASE_URL``
    when they are imported, and conftest files load before the test modules.
    The server is reached with asyncpg from the URL's host, port, user and
    password only; query parameters (ssl, unix sockets) are not supported.
    """
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    base_url = os.environ.get("TEST_DATABASE_URL")
    if not base_url:
        return
    if not worker:
        _claim_database(base_url, strict=False)
        return

    import asyncio

    import asyncpg  # type: ignore[import-untyped]
    from sqlalchemy.engine import make_url

    from tests._worker_db import worker_database_url

    try:
        worker_url = worker_database_url(base_url, worker)
    except ValueError as error:
        pytest.exit(f"Cannot isolate the {worker} test database: {error}", 4)
    target = make_url(base_url)
    worker_database = worker_url.database or ""

    async def create_if_missing() -> None:
        connection = await asyncpg.connect(
            host=target.host,
            port=target.port or 5432,
            user=target.username,
            password=target.password,
            database=target.database,
            timeout=10,
        )
        try:
            # The base database carries a statement_timeout (tests/db_guard.py); CREATE DATABASE may wait for
            # a checkpoint on a slow disk, so this connection runs without one.
            await connection.execute("SET statement_timeout = 0")
            exists = await connection.fetchval(
                "SELECT 1 FROM pg_database WHERE datname = $1", worker_database
            )
            if not exists:
                quoted = worker_database.replace('"', '""')
                try:
                    await connection.execute(f'CREATE DATABASE "{quoted}"')
                except asyncpg.DuplicateDatabaseError:
                    pass  # another checkout created it between the check and now
        finally:
            await connection.close()

    try:
        asyncio.run(create_if_missing())
    except (OSError, TimeoutError, asyncpg.PostgresError) as error:
        pytest.exit(
            f"Cannot create the {worker} test database on {target.host}: "
            f"{type(error).__name__}",
            4,
        )
    worker_url_text = worker_url.render_as_string(hide_password=False)
    _claim_database(worker_url_text, strict=True)
    os.environ["TEST_DATABASE_URL"] = worker_url_text


_isolate_worker_database()
