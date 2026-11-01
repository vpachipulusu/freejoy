"""``commands.make_admin`` against a real Postgres: the SQL, the row shape and the audit row.

Same contract as ``tests/api/test_admin_users_e2e.py``: runs only when ``TEST_DATABASE_URL`` points at
a throwaway local ``*_test`` database, which it rebuilds with the real Alembic migrations.
"""

import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.core.database import build_engine
from app.core.ids import new_id
from app.core.permissions import ADMIN_PERMISSIONS, effective_permissions
from app.repositories.admin import AdminRepository
from app.repositories.admin_users import AdminUsersRepository
from app.repositories.audit import AuditRepository
from app.services.audit import AuditService
from commands.make_admin import MakeAdminResult, Outcome, make_admin

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
BACKEND_ROOT = Path(__file__).resolve().parents[2]
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not set"
)


def _migrate(url: str, *args: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_ROOT,
        env={**os.environ, "DATABASE_URL_DIRECT": url},
        check=True,
        capture_output=True,
    )


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    assert TEST_DATABASE_URL is not None
    target = make_url(TEST_DATABASE_URL)
    assert (target.database or "").endswith("_test"), "database must end in _test"
    assert target.host in LOCAL_HOSTS, "refusing to rebuild a non-local database"
    _migrate(TEST_DATABASE_URL, "downgrade", "base")
    _migrate(TEST_DATABASE_URL, "upgrade", "head")
    yield TEST_DATABASE_URL


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    built = build_engine(database_url)
    async with built.begin() as connection:
        await connection.execute(text("TRUNCATE users, audit_logs CASCADE"))
    yield built
    await built.dispose()


async def add_user(engine: AsyncEngine, email: str) -> str:
    user_id = new_id()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO users (id, firebase_uid, email, email_verified) "
                "VALUES (:id, :uid, :email, true)"
            ),
            {"id": user_id, "uid": f"fb-{user_id}", "email": email},
        )
    return user_id


async def run(
    engine: AsyncEngine, email: str, role: str = "admin", *, dry_run: bool = False
) -> MakeAdminResult:
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        result = await make_admin(
            AdminUsersRepository(session),
            AuditService(AuditRepository(session)),
            session,
            email,
            role,
            dry_run=dry_run,
        )
        await session.rollback()  # a no-op after a commit; releases any lock otherwise
        return result


async def rows(engine: AsyncEngine, sql: str, **params: Any) -> list[Any]:
    async with engine.connect() as connection:
        return list((await connection.execute(text(sql), params)).all())


async def test_grant_writes_the_row_the_app_authorises_from(
    engine: AsyncEngine,
) -> None:
    user_id = await add_user(engine, "Ann@Example.com")

    result = await run(engine, "  ann@example.COM ")

    assert result.outcome is Outcome.GRANTED
    stored = await rows(
        engine,
        "SELECT role, permissions FROM admin_users WHERE user_id = :u",
        u=user_id,
    )
    assert stored[0][0] == "admin"
    assert sorted(stored[0][1]) == sorted(ADMIN_PERMISSIONS)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        grant = await AdminRepository(session).get_grant(f"fb-{user_id}")
    assert grant is not None
    assert effective_permissions(grant.role, grant.permissions) == ADMIN_PERMISSIONS


async def test_grant_writes_an_audit_row_with_no_actor(engine: AsyncEngine) -> None:
    user_id = await add_user(engine, "ann@example.com")

    await run(engine, "ann@example.com", "super_admin")

    audit = await rows(
        engine,
        "SELECT user_id, action, resource, resource_id, metadata FROM audit_logs",
    )
    assert len(audit) == 1
    actor, action, resource, resource_id, metadata = audit[0]
    assert (actor, action, resource, resource_id) == (
        None,
        "admin.users.grant_admin",
        "admin_users",
        user_id,
    )
    assert metadata["actor"] == "commands.make_admin"
    assert metadata["after"] == {"role": "super_admin", "permissions": ["*"]}
    assert "ann@example.com" not in str(metadata)


async def test_a_second_run_changes_nothing_and_a_role_change_updates_in_place(
    engine: AsyncEngine,
) -> None:
    await add_user(engine, "ann@example.com")
    await run(engine, "ann@example.com")

    again = await run(engine, "ann@example.com")
    promoted = await run(engine, "ann@example.com", "super_admin")

    assert again.outcome is Outcome.ALREADY_ADMIN
    assert (promoted.outcome, promoted.previous_role) == (Outcome.UPDATED, "admin")
    assert await rows(engine, "SELECT role, permissions FROM admin_users") == [
        ("super_admin", ["*"])
    ]
    assert len(await rows(engine, "SELECT 1 FROM audit_logs")) == 2


async def test_unknown_email_and_dry_run_write_nothing(engine: AsyncEngine) -> None:
    await add_user(engine, "ann@example.com")

    missing = await run(engine, "nobody@example.com")
    dry = await run(engine, "ann@example.com", dry_run=True)

    assert missing.outcome is Outcome.NOT_FOUND
    assert dry.outcome is Outcome.GRANTED
    assert await rows(engine, "SELECT 1 FROM admin_users") == []
    assert await rows(engine, "SELECT 1 FROM audit_logs") == []


async def test_the_last_super_admin_cannot_be_demoted(engine: AsyncEngine) -> None:
    await add_user(engine, "ann@example.com")
    await run(engine, "ann@example.com", "super_admin")

    refused = await run(engine, "ann@example.com", "admin")

    assert refused.outcome is Outcome.LAST_SUPER_ADMIN
    assert await rows(engine, "SELECT role FROM admin_users") == [("super_admin",)]


async def test_two_users_differing_only_in_case_are_refused(
    engine: AsyncEngine,
) -> None:
    await add_user(engine, "ann@example.com")
    await add_user(engine, "ANN@example.com")

    result = await run(engine, "ann@example.com")

    assert result.outcome is Outcome.AMBIGUOUS
    assert await rows(engine, "SELECT 1 FROM admin_users") == []
