import os
import ssl
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from sqlalchemy.engine import URL, make_url

ASYNC_DRIVER = "postgresql+asyncpg"

# Query parameters that asyncpg understands as connect arguments. libpq-style
# `sslmode` is translated to asyncpg's `ssl`; every other parameter (for example
# `channel_binding`, `options`) is dropped because asyncpg rejects it.
_TLS_PARAMS = ("ssl", "sslmode")

# Neon pooled endpoints sit behind PgBouncer in transaction mode, which cannot keep
# server-side prepared statements across transactions. Both caches are off (asyncpg's
# own and the SQLAlchemy dialect's), and each statement gets a unique name so two
# client connections sharing one server connection never collide (SQLAlchemy's
# documented PgBouncer setup for asyncpg).
STATEMENT_CACHE_SIZE = 0

# Seconds asyncpg waits to open a connection before raising TimeoutError (its
# default is 60). Long enough for a Neon compute to wake from suspend.
CONNECT_TIMEOUT_SECONDS = 10.0


def unique_prepared_statement_name() -> str:
    """A prepared statement name that cannot clash behind PgBouncer."""
    return f"__asyncpg_{uuid4()}__"


@dataclass(frozen=True)
class AsyncDatabaseTarget:
    """An asyncpg URL (password intact) and its connect args.

    The connect args carry the TLS mode, the PgBouncer-safe statement settings and the
    connect timeout. The app engines (``build_engine``) and Alembic both use them.
    """

    url: URL
    connect_args: dict[str, Any] = field(default_factory=dict)


def _tls_connect_arg(mode: str) -> Any:
    """The asyncpg ``ssl`` argument for a libpq ``sslmode``.

    asyncpg treats the string ``verify-full`` like libpq: it trusts only the file named by ``PGSSLROOTCERT`` or
    ``~/.postgresql/root.crt`` and never the system CA store, so on a host with neither (a serverless function)
    it fails to connect ("root certificate file ... does not exist"). Without ``PGSSLROOTCERT`` the mode becomes a
    default SSL context, which trusts the system CA store, requires a certificate and checks the host name: what
    ``verify-full`` means for a host with a public-CA certificate. ``require`` still verifies nothing.
    """
    if mode == "verify-full" and not os.environ.get("PGSSLROOTCERT"):
        return ssl.create_default_context()
    return mode


def to_async_target(raw_url: str) -> AsyncDatabaseTarget:
    """Rebuild a Postgres URL for asyncpg without losing TLS settings.

    Accepts `postgresql://`, `postgres://` or `postgresql+asyncpg://`, with or
    without `?sslmode=require` (the form Neon hands out).
    """
    parsed = make_url(raw_url)
    tls_mode = next(
        (str(parsed.query[key]) for key in _TLS_PARAMS if key in parsed.query), None
    )
    url = parsed.set(drivername=ASYNC_DRIVER, query={})
    tls_args: dict[str, Any] = (
        {} if tls_mode is None else {"ssl": _tls_connect_arg(tls_mode)}
    )
    connect_args: dict[str, Any] = {
        "statement_cache_size": STATEMENT_CACHE_SIZE,
        "prepared_statement_cache_size": STATEMENT_CACHE_SIZE,
        "prepared_statement_name_func": unique_prepared_statement_name,
        "timeout": CONNECT_TIMEOUT_SECONDS,
        **tls_args,
    }
    return AsyncDatabaseTarget(url=url, connect_args=connect_args)
