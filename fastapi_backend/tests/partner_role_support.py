"""Apply ``commands/sql/partner_role.sql`` to a test database and connect as the role it creates.

Roles are cluster-wide, and several developers and the gate share one test cluster, so a test must never create or
drop a role named plainly ``freejoy_partner``. ``role_name_for`` gives a name unique to the test database
(``freejoy_partner_<database>``): created if absent by the script, never dropped, with grants applied only inside
its own database.

The script is a psql script. psql is not assumed to be installed, so the parameter block (between the two marker
comments) is replaced by the two ``set_config`` calls it stands for and the rest runs as plain SQL over asyncpg.
"""

import hashlib
import re
from pathlib import Path

import asyncpg  # type: ignore[import-untyped]
from sqlalchemy.engine import URL, make_url

SCRIPT = Path(__file__).resolve().parents[1] / "commands" / "sql" / "partner_role.sql"
BEGIN_MARKER = "-- parameters (begin)"
END_MARKER = "-- parameters (end)"
TX_BEGIN_MARKER = "-- transaction end (begin)"
TX_END_MARKER = "-- transaction end (end)"
DEFAULT_CONNECTION_LIMIT = 20
MAX_ROLE_LENGTH = 63


def role_name_for(database: str) -> str:
    """``freejoy_partner_<database>``, shortened with a hash when it would not fit a role name."""
    clean = re.sub(r"[^a-z0-9_]", "_", database.lower())
    name = f"freejoy_partner_{clean}"
    if len(name) <= MAX_ROLE_LENGTH:
        return name
    digest = hashlib.sha256(database.encode()).hexdigest()[:10]
    return f"freejoy_partner_{clean[:30]}_{digest}"


def role_password_for(role: str) -> str:
    """A test-only password, the same every run (the role persists between runs)."""
    return f"pw-{hashlib.sha256(role.encode()).hexdigest()[:24]}"


def _without_block(text: str, begin: str, end: str) -> str:
    start = text.index(begin)
    stop = text.index(end) + len(end)
    return text[:start] + text[stop:]


def script_body() -> str:
    """The script without its psql-only blocks (the parameters and the closing COMMIT)."""
    text = SCRIPT.read_text(encoding="utf-8")
    text = _without_block(text, BEGIN_MARKER, END_MARKER)
    return _without_block(text, TX_BEGIN_MARKER, TX_END_MARKER)


def asyncpg_dsn(
    url: URL, user: str | None = None, password: str | None = None
) -> dict[str, object]:
    return {
        "host": url.host,
        "port": url.port or 5432,
        "user": user or url.username,
        "password": password if password is not None else url.password,
        "database": url.database,
        "timeout": 10,
    }


async def apply_partner_role(
    owner_url: str,
    role: str,
    password: str,
    connection_limit: int = DEFAULT_CONNECTION_LIMIT,
) -> None:
    """Run the committed script as the database owner for ``role``, in one transaction like the real run."""
    url = make_url(owner_url)
    connection = await asyncpg.connect(**asyncpg_dsn(url))
    try:
        async with connection.transaction():
            await connection.execute(
                "SELECT set_config('freejoy.partner_role', $1, true), "
                "set_config('freejoy.partner_password', $2, true), "
                "set_config('freejoy.partner_connection_limit', $3, true)",
                role,
                password,
                str(connection_limit),
            )
            await connection.execute(script_body())
    finally:
        await connection.close()


def role_url(owner_url: str, role: str, password: str) -> str:
    """The owner's URL with the role's own user and password."""
    return (
        make_url(owner_url)
        .set(username=role, password=password)
        .render_as_string(hide_password=False)
    )
