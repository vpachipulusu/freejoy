"""End to end: the in-app notifications API and migration 0009 on a real Postgres.

Same contract as ``test_audit_logs_e2e.py``: runs only when ``TEST_DATABASE_URL`` points
at a throwaway local ``*_test`` database, rebuilt with the real Alembic migrations.
Only Firebase is replaced (the session identity comes from an ``X-Test-Uid`` header).
"""

import os
import time
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
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
from tests.alembic_support import run_alembic

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
BACKEND_ROOT = Path(__file__).resolve().parents[2]
KEY = "test-only-internal-service-key-not-for-deployment"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
URL = "/v1/notifications"
T0 = datetime(2026, 1, 1, tzinfo=UTC)

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
            text("TRUNCATE users, organizations, notifications CASCADE")
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

    # Override the session identity (not get_current_identity) so the real email
    # verification check stays in play; `X-Test-Unverified` flips the claim.
    async def identity(
        x_test_uid: Annotated[str | None, Header()] = None,
        x_test_unverified: Annotated[str | None, Header()] = None,
    ) -> VerifiedIdentity:
        if x_test_uid is None:
            raise HTTPException(401, "Invalid authentication")
        return VerifiedIdentity(
            x_test_uid,
            f"{x_test_uid}@example.com",
            x_test_unverified is None,
            int(time.time()),
        )

    app.dependency_overrides[get_session_identity] = identity
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(
        transport=transport, base_url="http://test", headers={"X-Internal-Key": KEY}
    ) as http:
        yield http


async def seed_user(engine: AsyncEngine, name: str) -> str:
    user_id = new_id()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO users (id, firebase_uid, email, email_verified) "
                "VALUES (:id, :uid, :email, true)"
            ),
            {"id": user_id, "uid": f"fb-{name}", "email": f"{name}@example.com"},
        )
    return user_id


async def seed_org(engine: AsyncEngine, tag: str) -> str:
    org_id = new_id()
    async with engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO organizations (id, name, slug) VALUES (:i, :n, :s)"),
            {"i": org_id, "n": tag, "s": f"{tag}-{org_id}".lower()},
        )
    return org_id


async def seed_notification(
    engine: AsyncEngine, user_id: str, minutes: int = 0, **columns: Any
) -> str:
    row: dict[str, Any] = {
        "id": new_id(),
        "user_id": user_id,
        "organization_id": None,
        "type": "info",
        "title": "Title",
        "message": "Message",
        "read": False,
        "link": None,
        "created_at": T0 + timedelta(minutes=minutes),
        **columns,
    }
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO notifications (id, user_id, organization_id, "
                "type, title, message, read, link, created_at) VALUES "
                "(:id, :user_id, :organization_id, :type, :title, "
                ":message, :read, :link, :created_at)"
            ),
            row,
        )
    return str(row["id"])


def as_user(name: str) -> dict[str, str]:
    return {"X-Test-Uid": f"fb-{name}"}


async def is_read(engine: AsyncEngine, notification_id: str) -> bool | None:
    async with engine.connect() as connection:
        result = await connection.execute(
            text("SELECT read FROM notifications WHERE id = :i"), {"i": notification_id}
        )
        row = result.first()
        return None if row is None else bool(row[0])


async def get_data(client: AsyncClient, name: str, **params: Any) -> dict[str, Any]:
    response = await client.get(URL, params=params, headers=as_user(name))
    assert response.status_code == 200
    data: dict[str, Any] = response.json()["data"]
    return data


async def test_anonymous_gets_401_on_every_route(client: AsyncClient) -> None:
    assert (await client.get(URL)).status_code == 401
    assert (await client.patch(f"{URL}/x/read")).status_code == 401
    assert (await client.post(f"{URL}/read-all")).status_code == 401
    assert (await client.delete(f"{URL}/x")).status_code == 401


async def test_list_returns_only_the_callers_notifications_newest_first(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    ada = await seed_user(engine, "ada")
    bob = await seed_user(engine, "bob")
    older = await seed_notification(engine, ada, 1, title="older")
    newer = await seed_notification(engine, ada, 2, title="newer", read=True)
    await seed_notification(engine, bob, 3, title="bobs-secret")

    response = await client.get(URL, headers=as_user("ada"))

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    data = response.json()["data"]
    assert [n["id"] for n in data["notifications"]] == [newer, older]
    assert data["total_count"] == 2
    assert data["unread_count"] == 1
    assert "bobs-secret" not in response.text
    assert set(data["notifications"][0]) == {
        "id",
        "type",
        "title",
        "message",
        "read",
        "link",
        "organization_id",
        "created_at",
    }


async def test_list_filters_by_unread_and_organisation_with_paging(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    ada = await seed_user(engine, "ada")
    org_a = await seed_org(engine, "a")
    org_b = await seed_org(engine, "b")
    personal = await seed_notification(engine, ada, 1)
    org_note = await seed_notification(engine, ada, 2, organization_id=org_a)
    read_note = await seed_notification(
        engine, ada, 3, organization_id=org_a, read=True
    )
    await seed_notification(engine, ada, 4, organization_id=org_b)

    by_org = await get_data(client, "ada", organization_id=org_a)
    unread = await get_data(client, "ada", unread_only="true")
    page = await get_data(client, "ada", limit=1, offset=1)

    assert [n["id"] for n in by_org["notifications"]] == [read_note, org_note]
    assert by_org["unread_count"] == 1
    unread_ids = [n["id"] for n in unread["notifications"]]
    assert read_note not in unread_ids and personal in unread_ids
    assert unread["total_count"] == 3
    assert len(page["notifications"]) == 1 and page["total_count"] == 4


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
        {"organization_id": ""},
        {"unread_only": "maybe"},
    ],
)
async def test_list_rejects_invalid_query(
    client: AsyncClient, engine: AsyncEngine, params: dict[str, Any]
) -> None:
    await seed_user(engine, "ada")

    response = await client.get(URL, params=params, headers=as_user("ada"))

    assert response.status_code == 422


async def test_mark_read_updates_only_the_callers_notification(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    ada = await seed_user(engine, "ada")
    bob = await seed_user(engine, "bob")
    mine = await seed_notification(engine, ada)
    theirs = await seed_notification(engine, bob)

    ok = await client.patch(f"{URL}/{mine}/read", headers=as_user("ada"))
    again = await client.patch(f"{URL}/{mine}/read", headers=as_user("ada"))
    foreign = await client.patch(f"{URL}/{theirs}/read", headers=as_user("ada"))
    missing = await client.patch(f"{URL}/nope/read", headers=as_user("ada"))

    assert ok.status_code == 200 and ok.json()["data"]["read"] is True
    assert again.status_code == 200
    assert foreign.status_code == 404 and missing.status_code == 404
    assert foreign.json() == missing.json()
    assert await is_read(engine, mine) is True
    assert await is_read(engine, theirs) is False


async def test_mark_all_read_is_scoped_to_the_caller_and_optional_org(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    ada = await seed_user(engine, "ada")
    bob = await seed_user(engine, "bob")
    org_a = await seed_org(engine, "a")
    personal = await seed_notification(engine, ada, 1)
    in_org = await seed_notification(engine, ada, 2, organization_id=org_a)
    theirs = await seed_notification(engine, bob, 4, organization_id=org_a)

    scoped = await client.post(
        f"{URL}/read-all", params={"organization_id": org_a}, headers=as_user("ada")
    )
    assert scoped.json()["data"]["updated_count"] == 1
    assert await is_read(engine, in_org) is True
    assert await is_read(engine, personal) is False

    everything = await client.post(f"{URL}/read-all", headers=as_user("ada"))

    assert everything.status_code == 200
    assert everything.json()["data"]["updated_count"] == 1
    assert await is_read(engine, personal) is True
    assert await is_read(engine, in_org) is True
    assert await is_read(engine, theirs) is False


async def test_delete_removes_only_the_callers_notification(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    ada = await seed_user(engine, "ada")
    bob = await seed_user(engine, "bob")
    mine = await seed_notification(engine, ada)
    theirs = await seed_notification(engine, bob)

    ok = await client.delete(f"{URL}/{mine}", headers=as_user("ada"))
    gone = await client.delete(f"{URL}/{mine}", headers=as_user("ada"))
    foreign = await client.delete(f"{URL}/{theirs}", headers=as_user("ada"))

    assert ok.status_code == 200 and ok.json() == {"success": True}
    assert gone.status_code == 404 and foreign.status_code == 404
    assert await is_read(engine, mine) is None
    assert await is_read(engine, theirs) is False


async def test_user_without_platform_row_gets_404_on_every_route(
    client: AsyncClient,
) -> None:
    ghost = as_user("ghost")

    assert (await client.get(URL, headers=ghost)).status_code == 404
    assert (await client.post(f"{URL}/read-all", headers=ghost)).status_code == 404
    assert (await client.patch(f"{URL}/n1/read", headers=ghost)).status_code == 404
    assert (await client.delete(f"{URL}/n1", headers=ghost)).status_code == 404


async def test_other_users_rows_in_the_same_org_never_appear(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    ada = await seed_user(engine, "ada")
    bob = await seed_user(engine, "bob")
    org_a = await seed_org(engine, "a")
    mine = await seed_notification(engine, ada, 1, organization_id=org_a)
    theirs = await seed_notification(engine, bob, 2, organization_id=org_a)

    by_org = await get_data(client, "ada", organization_id=org_a)
    scoped = await client.post(
        f"{URL}/read-all", params={"organization_id": org_a}, headers=as_user("ada")
    )

    assert [n["id"] for n in by_org["notifications"]] == [mine]
    assert by_org["total_count"] == 1 and by_org["unread_count"] == 1
    assert scoped.json()["data"]["updated_count"] == 1
    assert await is_read(engine, theirs) is False


async def test_unread_only_total_follows_the_filter_but_unread_count_does_not(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    ada = await seed_user(engine, "ada")
    await seed_notification(engine, ada, 1, read=True)
    await seed_notification(engine, ada, 2, read=True)
    await seed_notification(engine, ada, 3)

    data = await get_data(client, "ada", unread_only="true")

    assert len(data["notifications"]) == 1
    assert data["total_count"] == 1 and data["unread_count"] == 1
    assert (await get_data(client, "ada"))["total_count"] == 3


async def test_equal_timestamps_order_by_id_descending_and_paging_bounds(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    ada = await seed_user(engine, "ada")
    # Fixed lowercase-alphanumeric ids with one shared prefix sort the same under any
    # database collation, so the id DESC tie-break cannot flake.
    for number in range(1, 5):
        await seed_notification(engine, ada, 5, id=f"aa000{number}", read=number == 4)

    data = await get_data(client, "ada")
    first = await get_data(client, "ada", limit=1, offset=0)
    second = await get_data(client, "ada", limit=1, offset=1)
    unread_second = await get_data(client, "ada", unread_only="true", limit=1, offset=1)
    beyond = await get_data(client, "ada", offset=50)

    assert [n["id"] for n in data["notifications"]] == [
        "aa0004",
        "aa0003",
        "aa0002",
        "aa0001",
    ]
    assert [n["id"] for n in first["notifications"]] == ["aa0004"]
    assert [n["id"] for n in second["notifications"]] == ["aa0003"]
    # aa0004 is read, so the unread list is aa0003, aa0002, aa0001 and offset=1 is aa0002.
    assert [n["id"] for n in unread_second["notifications"]] == ["aa0002"]
    assert unread_second["total_count"] == 3
    assert beyond["notifications"] == [] and beyond["total_count"] == 4
    assert (await get_data(client, "ada", limit=100, offset=10_000))["total_count"] == 4
    over = await client.get(URL, params={"offset": 10_001}, headers=as_user("ada"))
    assert over.status_code == 422


async def test_mark_all_read_twice_and_for_an_empty_inbox_updates_nothing(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    ada = await seed_user(engine, "ada")
    await seed_notification(engine, ada)

    first = await client.post(f"{URL}/read-all", headers=as_user("ada"))
    second = await client.post(f"{URL}/read-all", headers=as_user("ada"))

    assert first.json()["data"]["updated_count"] == 1
    assert second.status_code == 200 and second.json()["data"]["updated_count"] == 0
    await seed_user(engine, "bob")
    empty = await client.post(f"{URL}/read-all", headers=as_user("bob"))
    assert empty.status_code == 200 and empty.json()["data"]["updated_count"] == 0


async def test_foreign_and_missing_delete_answer_identically(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    await seed_user(engine, "ada")
    bob = await seed_user(engine, "bob")
    theirs = await seed_notification(engine, bob)

    foreign = await client.delete(f"{URL}/{theirs}", headers=as_user("ada"))
    missing = await client.delete(f"{URL}/nope", headers=as_user("ada"))

    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()


@pytest.mark.parametrize("bad_id", ["bad%20id!", "x" * 129])
async def test_malformed_notification_ids_are_rejected(
    client: AsyncClient, engine: AsyncEngine, bad_id: str
) -> None:
    await seed_user(engine, "ada")

    patched = await client.patch(f"{URL}/{bad_id}/read", headers=as_user("ada"))
    deleted = await client.delete(f"{URL}/{bad_id}", headers=as_user("ada"))

    assert patched.status_code == deleted.status_code == 422


ROUTES = [
    ("GET", URL),
    ("POST", f"{URL}/read-all"),
    ("PATCH", f"{URL}/n1/read"),
    ("DELETE", f"{URL}/n1"),
]


@pytest.mark.parametrize(("method", "path"), ROUTES)
@pytest.mark.parametrize("key", [None, "wrong-key"])
async def test_internal_key_is_required(
    client: AsyncClient,
    engine: AsyncEngine,
    method: str,
    path: str,
    key: str | None,
) -> None:
    await seed_user(engine, "ada")
    client.headers.pop("X-Internal-Key")  # the fixture sends a valid key by default
    headers = (
        as_user("ada") if key is None else {**as_user("ada"), "X-Internal-Key": key}
    )

    response = await client.request(method, path, headers=headers)

    assert response.status_code == 401
    assert response.json() == {"success": False, "error": "Unauthorized"}


@pytest.mark.parametrize(("method", "path"), ROUTES)
async def test_unverified_email_gets_403_on_every_route(
    client: AsyncClient, engine: AsyncEngine, method: str, path: str
) -> None:
    ada = await seed_user(engine, "ada")
    note = await seed_notification(engine, ada)
    headers = {**as_user("ada"), "X-Test-Unverified": "1"}

    response = await client.request(method, path.replace("n1", note), headers=headers)

    assert response.status_code == 403
    assert await is_read(engine, note) is False
