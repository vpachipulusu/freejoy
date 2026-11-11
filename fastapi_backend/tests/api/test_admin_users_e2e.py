"""End to end: the admin users routes, dependency chain and audit trail on a real Postgres.

Same contract as ``test_stripe_webhook_e2e.py``: runs only when ``TEST_DATABASE_URL``
points at a throwaway local database whose name ends in "_test", and rebuilds its
schema with the real Alembic migrations. Only Firebase is replaced: the session
identity comes from an ``X-Test-Uid`` header and account changes go to a recorder.
Everything else (``require_admin``, the audit service, the repositories, the
transaction handling) is the production code.
"""

import asyncio
import json
import os
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Annotated, Any, ClassVar

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
from app.repositories.audit import AuditRepository
from tests.alembic_support import run_alembic

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
BACKEND_ROOT = Path(__file__).resolve().parents[2]
KEY = "test-only-internal-service-key-not-for-deployment"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
MANAGER = [
    "users.read",
    "users.update",
    "users.delete",
    "admins.create",
    "admins.delete",
]

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
        await connection.execute(text("TRUNCATE users, audit_logs CASCADE"))
    yield built
    await built.dispose()


class Accounts:
    """Stands in for FirebaseIdentityProvider and records what would be sent."""

    calls: ClassVar[list[tuple[str, str, bool | None]]] = []

    async def set_user_disabled(self, uid: str, disabled: bool) -> None:
        Accounts.calls.append(("disable", uid, disabled))

    async def revoke_refresh_tokens(self, uid: str) -> None:
        Accounts.calls.append(("revoke", uid, None))

    async def delete_user(self, uid: str) -> None:
        Accounts.calls.append(("delete", uid, None))


@pytest.fixture
async def client(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    Accounts.calls = []
    monkeypatch.setattr("app.api.v1.admin_users.FirebaseIdentityProvider", Accounts)
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


async def seed(
    engine: AsyncEngine,
    name: str,
    role: str | None = None,
    permissions: list[str] | None = None,
    **columns: Any,
) -> str:
    """Insert a user (firebase uid ``fb-<name>``) and, with a role, its admin grant."""
    user_id = new_id()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO users (id, firebase_uid, email, email_verified, "
                "display_name, two_factor_secret) VALUES (:id, :uid, :email, true, "
                ":name, :secret)"
            ),
            {
                "id": user_id,
                "uid": f"fb-{name}",
                "email": f"{name}@example.com",
                "name": name.title(),
                "secret": columns.get("secret"),
            },
        )
        if role:
            await connection.execute(
                text(
                    "INSERT INTO admin_users (id, user_id, role, permissions) "
                    "VALUES (:id, :user_id, :role, CAST(:perms AS jsonb))"
                ),
                {
                    "id": new_id(),
                    "user_id": user_id,
                    "role": role,
                    "perms": json.dumps(permissions or []),
                },
            )
    return user_id


async def rows(engine: AsyncEngine, sql: str, **params: Any) -> list[Any]:
    async with engine.connect() as connection:
        return list((await connection.execute(text(sql), params)).all())


async def admin_row(engine: AsyncEngine, user_id: str) -> tuple[str, Any] | None:
    found = await rows(
        engine,
        "SELECT role, permissions FROM admin_users WHERE user_id = :id",
        id=user_id,
    )
    return (found[0][0], found[0][1]) if found else None


async def audit_rows(engine: AsyncEngine, action: str | None = None) -> list[Any]:
    sql = "SELECT user_id, action, resource, resource_id, metadata FROM audit_logs"
    if action:
        return await rows(
            engine, sql + " WHERE action = :a ORDER BY created_at", a=action
        )
    return await rows(engine, sql + " ORDER BY created_at")


def as_user(name: str) -> dict[str, str]:
    return {"X-Test-Uid": f"fb-{name}", "User-Agent": "e2e-agent"}


async def test_unauthenticated_request_is_401(client, engine):
    result = await client.get("/v1/admin/users")

    assert result.status_code == 401


async def test_user_without_an_admin_record_is_403_with_no_audit_row(client, engine):
    await seed(engine, "plain")

    result = await client.get("/v1/admin/users", headers=as_user("plain"))

    assert result.status_code == 403
    assert result.json() == {"success": False, "error": "Forbidden"}
    assert await audit_rows(engine) == []


async def test_admin_missing_the_permission_is_403_and_changes_nothing(client, engine):
    await seed(engine, "reader", "admin", ["users.read"])
    target = await seed(engine, "bob")

    ban = await client.post(f"/v1/admin/users/{target}/ban", headers=as_user("reader"))
    delete = await client.delete(f"/v1/admin/users/{target}", headers=as_user("reader"))

    assert (ban.status_code, delete.status_code) == (403, 403)
    assert Accounts.calls == [] and await audit_rows(engine) == []
    assert await rows(engine, "SELECT 1 FROM users WHERE id = :id", id=target)


async def test_list_and_detail_return_real_rows_without_secrets(client, engine):
    await seed(engine, "reader", "admin", ["users.read"])
    target = await seed(engine, "bob", secret="JBSWY3DPEHPK3PXP-do-not-leak")

    listing = await client.get("/v1/admin/users?search=bob", headers=as_user("reader"))
    detail = await client.get(f"/v1/admin/users/{target}", headers=as_user("reader"))
    paged = await client.get("/v1/admin/users?limit=1", headers=as_user("reader"))

    assert listing.json()["data"]["total_count"] == 1
    assert listing.json()["data"]["users"][0]["email"] == "bob@example.com"
    assert detail.json()["data"]["id"] == target
    assert len(paged.json()["data"]["users"]) == 1
    assert paged.json()["data"]["total_count"] == 2
    for response in (listing, detail, paged):
        assert "do-not-leak" not in response.text
        assert "two_factor_secret" not in response.text


async def test_search_treats_like_wildcards_literally(client, engine):
    await seed(engine, "reader", "admin", ["users.read"])
    await seed(engine, "bob")

    result = await client.get("/v1/admin/users?search=%25", headers=as_user("reader"))

    assert result.json()["data"]["total_count"] == 0


async def test_admin_cannot_grant_themselves_the_wildcard(client, engine):
    me = await seed(engine, "mgr", "admin", MANAGER)

    attempts = [
        {"role": "super_admin", "permissions": ["*"]},
        {"role": "admin", "permissions": ["settings.write"]},
        {"role": "admin", "permissions": ["users.read"]},
    ]
    for body in attempts:
        result = await client.put(
            f"/v1/admin/users/{me}/admin", json=body, headers=as_user("mgr")
        )
        assert result.status_code in (403, 422)

    assert await admin_row(engine, me) == ("admin", MANAGER)
    assert await audit_rows(engine) == []


async def test_non_super_admin_cannot_grant_anyone_more_than_they_hold(client, engine):
    await seed(engine, "mgr", "admin", MANAGER)
    bob = await seed(engine, "bob")

    attempts = [
        {"role": "super_admin", "permissions": ["*"]},
        {"role": "super_admin", "permissions": ["users.read"]},
        {"role": "admin", "permissions": ["*"]},
        {"role": "admin", "permissions": ["settings.write", "users.read"]},
    ]
    for body in attempts:
        result = await client.put(
            f"/v1/admin/users/{bob}/admin", json=body, headers=as_user("mgr")
        )
        assert result.status_code in (403, 422)

    assert await admin_row(engine, bob) is None
    assert await audit_rows(engine) == []


async def test_grant_within_own_permissions_is_stored_and_audited(client, engine):
    mgr = await seed(engine, "mgr", "admin", MANAGER)
    bob = await seed(engine, "bob")

    result = await client.put(
        f"/v1/admin/users/{bob}/admin",
        json={"role": "admin", "permissions": ["users.update", "users.read"]},
        headers=as_user("mgr"),
    )

    assert result.status_code == 200
    assert await admin_row(engine, bob) == ("admin", ["users.read", "users.update"])
    ((actor, action, resource, resource_id, metadata),) = await audit_rows(engine)
    assert (actor, action, resource, resource_id) == (
        mgr,
        "admin.users.grant_admin",
        "admin_users",
        bob,
    )
    assert "before" not in metadata  # a first grant has no previous state
    assert metadata["after"] == {
        "role": "admin",
        "permissions": ["users.read", "users.update"],
    }
    assert metadata["actor_role"] == "admin"


async def test_super_admin_can_grant_the_wildcard_and_it_is_audited(client, engine):
    await seed(engine, "root", "super_admin", ["*"])
    bob = await seed(engine, "bob")

    result = await client.put(
        f"/v1/admin/users/{bob}/admin",
        json={"role": "super_admin", "permissions": ["*"]},
        headers=as_user("root"),
    )

    assert result.status_code == 200
    assert await admin_row(engine, bob) == ("super_admin", ["*"])
    ((_, action, _, _, metadata),) = await audit_rows(engine)
    assert action == "admin.users.grant_admin"
    assert metadata["after"]["role"] == "super_admin"


async def test_regrant_records_the_previous_grant_as_before(client, engine):
    await seed(engine, "mgr", "admin", MANAGER)
    peer = await seed(engine, "peer", "admin", ["users.read"])

    await client.put(
        f"/v1/admin/users/{peer}/admin",
        json={"role": "admin", "permissions": ["users.update"]},
        headers=as_user("mgr"),
    )

    ((_, _, _, _, metadata),) = await audit_rows(engine)
    assert metadata["before"] == {"role": "admin", "permissions": ["users.read"]}
    assert metadata["after"]["permissions"] == ["users.update"]


async def test_revoke_removes_the_grant_and_is_audited(client, engine):
    mgr = await seed(engine, "mgr", "admin", MANAGER)
    peer = await seed(engine, "peer", "admin", ["users.read"])

    result = await client.delete(
        f"/v1/admin/users/{peer}/admin", headers=as_user("mgr")
    )

    assert result.status_code == 200
    assert await admin_row(engine, peer) is None
    ((actor, action, _, resource_id, metadata),) = await audit_rows(engine)
    assert (actor, action, resource_id) == (mgr, "admin.users.revoke_admin", peer)
    assert metadata["before"] == {"role": "admin", "permissions": ["users.read"]}


async def test_revoking_oneself_is_refused(client, engine):
    me = await seed(engine, "mgr", "admin", MANAGER)

    result = await client.delete(f"/v1/admin/users/{me}/admin", headers=as_user("mgr"))

    assert result.status_code == 409
    assert await admin_row(engine, me) is not None
    assert await audit_rows(engine) == []


async def test_a_super_admin_cannot_revoke_or_demote_themselves(client, engine):
    root = await seed(engine, "root", "super_admin", ["*"])

    revoke = await client.delete(
        f"/v1/admin/users/{root}/admin", headers=as_user("root")
    )
    demote = await client.put(
        f"/v1/admin/users/{root}/admin",
        json={"role": "admin", "permissions": ["users.read"]},
        headers=as_user("root"),
    )

    assert (revoke.status_code, demote.status_code) == (409, 403)
    assert await admin_row(engine, root) == ("super_admin", ["*"])


async def test_manager_cannot_revoke_or_demote_a_super_admin(client, engine):
    await seed(engine, "mgr", "admin", MANAGER)
    root = await seed(engine, "root", "super_admin", ["*"])

    revoke = await client.delete(
        f"/v1/admin/users/{root}/admin", headers=as_user("mgr")
    )
    demote = await client.put(
        f"/v1/admin/users/{root}/admin",
        json={"role": "admin", "permissions": ["users.read"]},
        headers=as_user("mgr"),
    )

    assert (revoke.status_code, demote.status_code) == (403, 403)
    assert await admin_row(engine, root) == ("super_admin", ["*"])
    assert await audit_rows(engine) == []


async def test_two_super_admins_cannot_revoke_each_other_concurrently(client, engine):
    first = await seed(engine, "root1", "super_admin", ["*"])
    second = await seed(engine, "root2", "super_admin", ["*"])

    results = await asyncio.gather(
        client.delete(f"/v1/admin/users/{second}/admin", headers=as_user("root1")),
        client.delete(f"/v1/admin/users/{first}/admin", headers=as_user("root2")),
    )

    statuses = sorted(r.status_code for r in results)
    assert statuses[0] == 200 and statuses[1] in (403, 409)
    remaining = await rows(
        engine, "SELECT count(*) FROM admin_users WHERE role = 'super_admin'"
    )
    assert remaining[0][0] == 1


async def test_update_changes_the_row_and_audits_field_names_only(client, engine):
    await seed(engine, "mgr", "admin", MANAGER)
    bob = await seed(engine, "bob")

    result = await client.patch(
        f"/v1/admin/users/{bob}", json={"bio": "private words"}, headers=as_user("mgr")
    )

    assert result.status_code == 200
    assert (await rows(engine, "SELECT bio FROM users WHERE id = :id", id=bob))[0][
        0
    ] == ("private words")
    ((_, action, _, _, metadata),) = await audit_rows(engine)
    assert action == "admin.users.update"
    assert metadata["details"] == {"fields": ["bio"]}
    assert "private words" not in json.dumps(metadata)


async def test_ban_and_unban_call_firebase_and_write_audit_rows(client, engine):
    await seed(engine, "mgr", "admin", MANAGER)
    bob = await seed(engine, "bob")

    banned = await client.post(
        f"/v1/admin/users/{bob}/ban", json={"reason": "spam"}, headers=as_user("mgr")
    )
    unbanned = await client.post(f"/v1/admin/users/{bob}/unban", headers=as_user("mgr"))

    assert (banned.status_code, unbanned.status_code) == (200, 200)
    assert Accounts.calls == [
        ("disable", "fb-bob", True),
        ("revoke", "fb-bob", None),
        ("disable", "fb-bob", False),
    ]
    actions = [row[1] for row in await audit_rows(engine)]
    assert actions == ["admin.users.ban", "admin.users.unban"]


async def test_banning_oneself_is_refused(client, engine):
    me = await seed(engine, "mgr", "admin", MANAGER)

    result = await client.post(f"/v1/admin/users/{me}/ban", headers=as_user("mgr"))

    assert result.status_code == 409
    assert Accounts.calls == [] and await audit_rows(engine) == []


async def test_delete_removes_the_user_and_the_firebase_account(client, engine):
    await seed(engine, "mgr", "admin", MANAGER)
    bob = await seed(engine, "bob")

    result = await client.delete(f"/v1/admin/users/{bob}", headers=as_user("mgr"))

    assert result.status_code == 200
    assert not await rows(engine, "SELECT 1 FROM users WHERE id = :id", id=bob)
    assert Accounts.calls == [("delete", "fb-bob", None)]
    ((_, action, _, resource_id, _),) = await audit_rows(engine)
    assert (action, resource_id) == ("admin.users.delete", bob)


async def test_delete_of_an_organisation_owner_is_refused(client, engine):
    await seed(engine, "mgr", "admin", MANAGER)
    bob = await seed(engine, "bob")
    org = new_id()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO organizations (id, name, slug) VALUES (:id, 'Org', :slug)"
            ),
            {"id": org, "slug": f"org-{org}"},
        )
        await connection.execute(
            text(
                "INSERT INTO memberships (id, organization_id, user_id, role) "
                "VALUES (:id, :org, :user, 'OWNER')"
            ),
            {"id": new_id(), "org": org, "user": bob},
        )

    result = await client.delete(f"/v1/admin/users/{bob}", headers=as_user("mgr"))

    assert result.status_code == 409
    assert await rows(engine, "SELECT 1 FROM users WHERE id = :id", id=bob)
    assert Accounts.calls == [] and await audit_rows(engine) == []


async def test_a_failing_audit_write_rolls_the_change_back(client, engine, monkeypatch):
    await seed(engine, "mgr", "admin", MANAGER)
    bob = await seed(engine, "bob")

    async def broken(self: AuditRepository, **fields: Any) -> None:
        raise RuntimeError("audit table unavailable")

    monkeypatch.setattr(AuditRepository, "add", broken)
    result = await client.put(
        f"/v1/admin/users/{bob}/admin",
        json={"role": "admin", "permissions": ["users.read"]},
        headers=as_user("mgr"),
    )

    assert result.status_code == 500
    assert await admin_row(engine, bob) is None
    assert await audit_rows(engine) == []


async def test_a_super_admin_row_without_the_wildcard_has_no_extra_standing(
    client, engine
):
    await seed(engine, "hollow", "super_admin", ["admins.create"])
    bob = await seed(engine, "bob")
    mgr = await seed(engine, "mgr", "admin", MANAGER)

    wildcard = await client.put(
        f"/v1/admin/users/{bob}/admin",
        json={"role": "super_admin", "permissions": ["*"]},
        headers=as_user("hollow"),
    )
    beyond = await client.put(
        f"/v1/admin/users/{bob}/admin",
        json={"role": "admin", "permissions": ["settings.write"]},
        headers=as_user("hollow"),
    )
    revoke = await client.delete(
        f"/v1/admin/users/{mgr}/admin", headers=as_user("hollow")
    )

    assert (wildcard.status_code, beyond.status_code, revoke.status_code) == (
        403,
        403,
        403,
    )
    assert await admin_row(engine, bob) is None
    assert await admin_row(engine, mgr) is not None
    assert await audit_rows(engine) == []


async def test_hollow_super_admins_do_not_count_towards_the_last_super_admin(
    client, engine
):
    await seed(engine, "root1", "super_admin", ["*"])
    root2 = await seed(engine, "root2", "super_admin", ["*"])
    await seed(engine, "hollow", "super_admin", ["admins.create"])

    first = await client.delete(
        f"/v1/admin/users/{root2}/admin", headers=as_user("root1")
    )

    assert first.status_code == 200
    assert await admin_row(engine, root2) is None


async def test_super_admin_role_must_be_exactly_the_wildcard(client, engine):
    await seed(engine, "root", "super_admin", ["*"])
    bob = await seed(engine, "bob")

    result = await client.put(
        f"/v1/admin/users/{bob}/admin",
        json={"role": "super_admin", "permissions": ["users.read"]},
        headers=as_user("root"),
    )

    assert result.status_code == 422
    assert await admin_row(engine, bob) is None
