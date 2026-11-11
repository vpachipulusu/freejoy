"""End to end: the personal data export on a real Postgres.

Same contract as ``test_notifications_e2e.py``: runs only when ``TEST_DATABASE_URL``
points at a throwaway local ``*_test`` database, rebuilt with the real Alembic
migrations. Only Firebase is replaced (the identity comes from an ``X-Test-Uid`` header).
"""

import os
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Annotated

import pytest
from fastapi import Header, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.api.auth import get_session_identity
from app.api.deps import get_session_provider
from app.core.database import DefaultSessionProvider, build_engine
from app.core.firebase import VerifiedIdentity
from app.core.ids import new_id
from app.main import create_app
from tests.alembic_support import run_alembic

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
BACKEND_ROOT = Path(__file__).resolve().parents[2]
KEY = "test-only-internal-service-key-not-for-deployment"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
URL = "/v1/me/export"

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not set"
)


def _migrate(url: str, *args: str) -> None:
    run_alembic(url, *args)


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    assert TEST_DATABASE_URL is not None
    target = make_url(TEST_DATABASE_URL)
    assert (target.database or "").endswith("_test"), (
        "refusing to rebuild a database whose name does not end in _test"
    )
    assert target.host in LOCAL_HOSTS, "refusing to rebuild a non-local database"
    _migrate(TEST_DATABASE_URL, "downgrade", "base")
    _migrate(TEST_DATABASE_URL, "upgrade", "head")
    yield TEST_DATABASE_URL


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    built = build_engine(database_url)
    async with built.begin() as connection:
        await connection.execute(
            text("TRUNCATE users, organizations, audit_logs CASCADE")
        )
    yield built
    await built.dispose()


@pytest.fixture
async def client(engine: AsyncEngine) -> AsyncIterator[AsyncClient]:
    app = create_app()
    provider = DefaultSessionProvider(
        async_sessionmaker(engine, expire_on_commit=False)
    )
    app.dependency_overrides[get_session_provider] = lambda: provider

    async def identity(
        x_test_uid: Annotated[str | None, Header()] = None,
    ) -> VerifiedIdentity:
        if x_test_uid is None:
            raise HTTPException(401, "Invalid authentication")
        return VerifiedIdentity(
            x_test_uid, f"{x_test_uid}@example.com", True, int(time.time())
        )

    app.dependency_overrides[get_session_identity] = identity
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(
        transport=transport, base_url="http://test", headers={"X-Internal-Key": KEY}
    ) as http:
        yield http


async def seed_account(engine: AsyncEngine, name: str) -> dict[str, str]:
    """A user with a 2FA secret, an organisation with Stripe ids, and one row of each list."""
    ids = {key: new_id() for key in ("user", "org", "member", "login", "note")}
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO users (id, firebase_uid, email, email_verified, "
                "two_factor_enabled, two_factor_secret, display_name) "
                "VALUES (:id, :uid, :email, true, true, :secret, :name)"
            ),
            {
                "id": ids["user"],
                "uid": f"fb-{name}",
                "email": f"{name}@example.com",
                "secret": f"TOTP-SECRET-{name}",
                "name": f"Name {name}",
            },
        )
        await connection.execute(
            text(
                "INSERT INTO organizations (id, name, slug, stripe_customer_id, "
                "stripe_subscription_id) VALUES (:id, :name, :slug, :cus, :sub)"
            ),
            {
                "id": ids["org"],
                "name": f"Org {name}",
                "slug": f"org-{name}",
                "cus": f"cus_{name}",
                "sub": f"sub_{name}",
            },
        )
        await connection.execute(
            text(
                "INSERT INTO memberships (id, organization_id, user_id, role) "
                "VALUES (:id, :org, :user, 'OWNER')"
            ),
            {"id": ids["member"], "org": ids["org"], "user": ids["user"]},
        )
        await connection.execute(
            text(
                "INSERT INTO login_history (id, user_id, ip_address, successful) "
                "VALUES (:id, :user, :ip, true)"
            ),
            {"id": ids["login"], "user": ids["user"], "ip": f"10.0.0.{len(name)}"},
        )
        await connection.execute(
            text(
                "INSERT INTO notifications (id, user_id, type, title, message) "
                "VALUES (:id, :user, 'info', :title, 'hello')"
            ),
            {"id": ids["note"], "user": ids["user"], "title": f"Note {name}"},
        )
    return ids


async def test_export_has_only_the_callers_rows_and_no_secrets(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    alice = await seed_account(engine, "alice")
    bob = await seed_account(engine, "bobby")

    result = await client.post(URL, headers={"X-Test-Uid": "fb-alice"})

    assert result.status_code == 200
    data = result.json()["data"]
    assert data["profile"]["id"] == alice["user"]
    assert [m["organization_id"] for m in data["memberships"]] == [alice["org"]]
    assert [entry["id"] for entry in data["login_history"]] == [alice["login"]]
    assert [n["id"] for n in data["notifications"]] == [alice["note"]]
    assert data["truncated"] is False
    body = result.text
    for other_users_value in (bob["user"], bob["org"], "bobby", "Org bobby"):
        assert other_users_value not in body
    for secret in (
        "TOTP-SECRET",
        "cus_",
        "sub_",
        "fb-alice",
        "firebase_uid",
        "two_factor_secret",
    ):
        assert secret not in body
    assert data["profile"]["two_factor_enabled"] is True

    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text("SELECT user_id, action, resource_id FROM audit_logs")
            )
        ).all()
    assert [tuple(row) for row in rows] == [(alice["user"], "me.export", alice["user"])]


async def test_export_without_a_session_is_401_and_writes_no_audit(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    result = await client.post(URL)

    assert result.status_code == 401
    async with engine.connect() as connection:
        count = (
            await connection.execute(text("SELECT count(*) FROM audit_logs"))
        ).scalar_one()
    assert count == 0
