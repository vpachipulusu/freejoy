"""Create and drop throwaway databases for the real-Postgres suites.

Shared by ``tests/api/test_tenant_isolation_*_e2e.py`` and
``tests/services/test_store_migrations_e2e.py``. Every helper refuses to act unless
``TEST_DATABASE_URL`` names a ``*_test`` database on a local host, so a mistyped URL can never
create or drop databases on a remote server (a local server's other ``*_test`` databases
are not protected by this check; the sweep only touches the ``ks57``/``ks58`` names the
scaffold generates, and per-store ``store_<id>`` databases of a killed run are not swept).

Database names are ``<prefix>_<unix seconds>_<random>_<tag>``. The timestamp lets
``sweep_stale_databases`` remove what a killed run left behind without touching the
databases of a run that is still going (another pytest-xdist worker).
"""

import os
import re
import time
from collections.abc import Iterable

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.base import Base
from app.core.ids import new_id
from tests._worker_db import LOCAL_HOSTS

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
STALE_AFTER_SECONDS = 3600
_EPOCH_DIGITS = 10
CONNECT_TIMEOUT_SECONDS = 10


def require_local_test_server() -> str:
    """The server URL, or a test failure when it is not a local ``*_test`` database."""
    if TEST_DATABASE_URL is None:
        pytest.skip("TEST_DATABASE_URL is not set")
    target = make_url(TEST_DATABASE_URL)
    if not (target.database or "").endswith("_test") or target.host not in LOCAL_HOSTS:
        pytest.fail(
            "refusing to create and drop databases outside a local *_test server"
        )
    return TEST_DATABASE_URL


def url_for(database: str, password: str | None = None) -> str:
    """The test server's URL pointed at ``database`` (optionally with another password)."""
    url = make_url(require_local_test_server()).set(database=database)
    if password is not None:
        url = url.set(password=password)
    return url.render_as_string(False)


def unique_name(prefix: str, tag: str) -> str:
    return f"{prefix}_{int(time.time())}_{new_id()[:6].lower()}_{tag}"


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


async def create_database(server: AsyncEngine, name: str) -> None:
    async with server.execution_options(isolation_level="AUTOCOMMIT").connect() as c:
        await c.execute(text(f"CREATE DATABASE {quote(name)}"))


async def create_platform_database(server: AsyncEngine, name: str) -> AsyncEngine:
    """Create ``name`` with every platform table from the models and return an engine."""
    await create_database(server, name)
    platform = create_async_engine(
        url_for(name), connect_args={"timeout": CONNECT_TIMEOUT_SECONDS}
    )
    try:
        async with platform.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
    except BaseException:
        await platform.dispose()
        raise
    return platform


async def drop_databases(server: AsyncEngine, names: Iterable[str]) -> None:
    """Drop every database; one failing drop does not stop the rest (all errors raised)."""
    errors: list[Exception] = []
    for name in names:
        try:
            async with server.execution_options(
                isolation_level="AUTOCOMMIT"
            ).connect() as c:
                await c.execute(
                    text(f"DROP DATABASE IF EXISTS {quote(name)} WITH (FORCE)")
                )
        except Exception as exc:  # noqa: BLE001  (collected and re-raised below)
            errors.append(exc)
    if errors:
        raise ExceptionGroup("could not drop test databases", errors)


def _is_stale(name: str, prefix: str, now: float) -> bool:
    """Only names this scaffold generated (``<prefix>_<epoch>_<6 hex>_<tag>``) can be stale."""
    match = re.fullmatch(
        rf"{re.escape(prefix)}_(\d{{{_EPOCH_DIGITS}}})_[0-9a-z]{{6}}_\w+", name
    )
    return match is not None and now - int(match.group(1)) > STALE_AFTER_SECONDS


async def sweep_stale_databases(server: AsyncEngine, prefix: str) -> None:
    """Drop ``<prefix>_*`` databases left by a run that was killed before cleaning up."""
    async with server.connect() as connection:
        found = await connection.execute(
            text("SELECT datname FROM pg_database WHERE starts_with(datname, :p)"),
            {"p": f"{prefix}_"},
        )
        names = [row[0] for row in found]
    now = time.time()
    await drop_databases(server, [n for n in names if _is_stale(n, prefix, now)])
