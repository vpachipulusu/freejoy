"""Shared fixtures and helpers for the admin catalogue ``*_e2e.py`` suites.

Same contract as ``test_admin_users_e2e.py``: everything runs only when ``TEST_DATABASE_URL``
points at a throwaway local ``*_test`` database, whose schema is rebuilt with the real Alembic
migrations. Only Firebase is replaced (the identity comes from an ``X-Test-Uid`` header);
``require_admin``, the audit service, the repositories and the transactions are production code.
"""

import json
import os
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Annotated, Any

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

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
BACKEND_ROOT = Path(__file__).resolve().parents[1]
KEY = "test-only-internal-service-key-not-for-deployment"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
EDITOR = [
    "offers.read",
    "offers.write",
    "offers.publish",
    "categories.write",
    "merchants.write",
]
TRUNCATE = "TRUNCATE users, audit_logs, offers, categories, merchants CASCADE"

requires_database = pytest.mark.skipif(
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
        await connection.execute(text(TRUNCATE))
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


async def seed_admin(
    engine: AsyncEngine, name: str, permissions: list[str] | None = None
) -> str:
    """Insert a user (firebase uid ``fb-<name>``) and, with permissions, an ``admin`` grant."""
    user_id = new_id()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO users (id, firebase_uid, email, email_verified, display_name) "
                "VALUES (:id, :uid, :email, true, :name)"
            ),
            {
                "id": user_id,
                "uid": f"fb-{name}",
                "email": f"{name}@example.com",
                "name": name.title(),
            },
        )
        if permissions is not None:
            await connection.execute(
                text(
                    "INSERT INTO admin_users (id, user_id, role, permissions) "
                    "VALUES (:id, :user_id, 'admin', CAST(:perms AS jsonb))"
                ),
                {"id": new_id(), "user_id": user_id, "perms": json.dumps(permissions)},
            )
    return user_id


async def rows(engine: AsyncEngine, sql: str, **params: Any) -> list[Any]:
    async with engine.connect() as connection:
        return list((await connection.execute(text(sql), params)).all())


async def audit_rows(engine: AsyncEngine, action: str | None = None) -> list[Any]:
    sql = "SELECT user_id, action, resource, resource_id, metadata FROM audit_logs"
    if action:
        return await rows(
            engine, sql + " WHERE action = :a ORDER BY created_at", a=action
        )
    return await rows(engine, sql + " ORDER BY created_at")


def as_user(name: str) -> dict[str, str]:
    return {"X-Test-Uid": f"fb-{name}", "User-Agent": "e2e-agent"}


CATEGORY_BODY = {
    "name": "Home & cleaning",
    "short_name": "Home",
    "description": "Things for the home",
    "intro": "Intro copy",
    "tint": "sage",
}


def offer_body(category_id: str, merchant_id: str, **changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "title": "Free Sample Box",
        "summary": "A box of samples",
        "category_id": category_id,
        "merchant_id": merchant_id,
        "offer_type": "FREE_SAMPLE",
        "image_tint": "sage",
        "image_label": "Free",
        "image_alt": "A sample box",
        "postage_kind": "FREE",
        "eligibility": "UK residents",
        "availability": "While stocks last",
        "source_url": "https://shop.example.com/box",
    }
    return {**body, **changes}


async def review_token(
    client: AsyncClient, headers: dict[str, str], offer_url: str
) -> str:
    """The review token the admin read returns right now (what an editor holds after loading)."""
    loaded = await client.get(offer_url, headers=headers)
    return str(loaded.json()["data"]["review_token"])


async def approve(
    client: AsyncClient,
    headers: dict[str, str],
    offer_url: str,
    step: str,
    **body: Any,
) -> Any:
    """Load the offer, then ``verify`` or ``publish`` it with the token just read."""
    token = await review_token(client, headers, offer_url)
    return await client.post(
        f"{offer_url}/{step}", json={"review_token": token, **body}, headers=headers
    )


async def create_references(
    client: AsyncClient, headers: dict[str, str]
) -> tuple[str, str]:
    """Create one category and one merchant through the API; return their ids."""
    category = await client.post(
        "/v1/admin/categories", json=CATEGORY_BODY, headers=headers
    )
    merchant = await client.post(
        "/v1/admin/merchants", json={"name": "Acme Ltd"}, headers=headers
    )
    return category.json()["data"]["id"], merchant.json()["data"]["id"]
