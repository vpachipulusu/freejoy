"""The admin partner key routes through the real dependency chain, with a fake service behind them (no database)."""

import time
from collections.abc import Iterator
from dataclasses import replace
from typing import Annotated, Any

import pytest
from fastapi import Header, HTTPException
from httpx import ASGITransport, AsyncClient

from app.api.admin import get_admin_repository
from app.api.auth import get_session_identity
from app.api.deps import get_session
from app.api.v1.admin_partner_keys import get_partner_key_service
from app.core.firebase import VerifiedIdentity
from app.main import create_app
from app.repositories.admin import AdminGrant
from app.services.admin_partner_keys import AdminPartnerKeyService
from tests.fakes_admin_catalogue import (
    FakeAuditor,
    FakeMerchantStore,
    FakeSession,
    merchant,
)
from tests.fakes_partner import FakeAdminKeyStore

KEY = "test-only-internal-service-key-not-for-deployment"
APP = create_app()  # built once: creating the app is the slow part
BASE = "/v1/admin/merchants/mer1/partner-keys"


class Harness:
    def __init__(self) -> None:
        self.keys = FakeAdminKeyStore()
        self.auditor = FakeAuditor()
        self.session = FakeSession()
        self.merchants = FakeMerchantStore(
            replace(merchant("mer1"), website="https://shop.example.com"),
            merchant("bare"),
        )
        self.grants: dict[str, list[str]] = {}
        grants = self.grants

        class Admins:
            async def get_grant(self, uid: str) -> AdminGrant | None:
                name = uid.removeprefix("uid-")
                if name not in grants:
                    return None
                return AdminGrant(name, "admin", grants[name])

        async def identity(
            x_test_uid: Annotated[str | None, Header()] = None,
        ) -> VerifiedIdentity:
            if x_test_uid is None:
                raise HTTPException(401, "Invalid authentication")
            return VerifiedIdentity(
                f"uid-{x_test_uid}", f"{x_test_uid}@example.com", True, int(time.time())
            )

        overrides = APP.dependency_overrides
        overrides[get_session_identity] = identity
        overrides[get_admin_repository] = lambda: Admins()
        overrides[get_session] = lambda: self.session
        overrides[get_partner_key_service] = lambda: AdminPartnerKeyService(
            self.keys, self.merchants, self.session, self.auditor, "test"
        )

    def client(self, actor: str | None = "editor") -> AsyncClient:
        headers = {"X-Internal-Key": KEY}
        if actor:
            headers["X-Test-Uid"] = actor
        transport = ASGITransport(app=APP, raise_app_exceptions=False)
        return AsyncClient(transport=transport, base_url="http://test", headers=headers)


@pytest.fixture
def harness() -> Iterator[Harness]:
    built = Harness()
    built.grants["editor"] = ["partner_keys.read", "partner_keys.write"]
    built.grants["reader"] = ["partner_keys.read"]
    built.grants["writer"] = ["partner_keys.write"]
    built.grants["catalogue"] = ["offers.read", "merchants.write"]
    yield built
    APP.dependency_overrides.clear()


async def create(harness: Harness, merchant_id: str = "mer1", **kw: Any) -> Any:
    async with harness.client() as client:
        return await client.post(
            f"/v1/admin/merchants/{merchant_id}/partner-keys",
            json={"label": "Feed"},
            **kw,
        )


async def test_create_returns_the_key_once_and_list_never_does(
    harness: Harness,
) -> None:
    created = await create(harness)
    async with harness.client() as client:
        listed = await client.get(BASE)

    assert created.status_code == 201
    api_key = created.json()["data"]["api_key"]
    assert api_key.startswith("fj_test_")
    assert created.headers["cache-control"] == "no-store"
    assert api_key not in listed.text
    assert [k["label"] for k in listed.json()["data"]] == ["Feed"]


async def test_revoke_returns_the_key_with_its_revoked_time(harness: Harness) -> None:
    created = await create(harness)
    key_id = created.json()["data"]["key"]["id"]

    async with harness.client() as client:
        revoked = await client.post(f"{BASE}/{key_id}/revoke")

    assert revoked.status_code == 200
    assert revoked.json()["data"]["revoked_at"] is not None


@pytest.mark.parametrize(
    ("method", "path", "code"),
    [
        ("GET", "/v1/admin/merchants/nope/partner-keys", 404),
        ("POST", "/v1/admin/merchants/nope/partner-keys", 404),
        ("POST", "/v1/admin/merchants/mer1/partner-keys/nokey/revoke", 404),
        ("POST", "/v1/admin/merchants/bare/partner-keys", 409),
    ],
)
async def test_a_refusal_is_a_coded_error_with_a_fixed_message(
    harness: Harness, method: str, path: str, code: int
) -> None:
    async with harness.client() as client:
        response = await client.request(method, path, json={"label": "Feed"})

    assert response.status_code == code
    body = response.json()
    assert body["success"] is False
    assert body["code"] in {"not_found", "merchant_no_website"}
    assert "nope" not in body["error"]


async def test_the_environment_that_cannot_name_a_key_is_a_503() -> None:
    harness = Harness()
    try:
        APP.dependency_overrides[get_partner_key_service] = lambda: (
            AdminPartnerKeyService(
                harness.keys, harness.merchants, harness.session, harness.auditor, None
            )
        )
        harness.grants["editor"] = ["partner_keys.write"]
        async with harness.client() as client:
            response = await client.post(BASE, json={"label": "Feed"})
    finally:
        APP.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["code"] == "keys_unavailable"


async def test_reading_needs_partner_keys_read_and_changing_needs_partner_keys_write(
    harness: Harness,
) -> None:
    async with harness.client("reader") as client:
        read = await client.get(BASE)
        write = await client.post(BASE, json={"label": "x"})
        revoke = await client.post(f"{BASE}/k/revoke")
    async with harness.client("writer") as client:
        list_as_writer = await client.get(BASE)
    async with harness.client(None) as client:
        anonymous = await client.get(BASE)

    assert read.status_code == 200
    assert write.status_code == revoke.status_code == 403
    assert list_as_writer.status_code == 403
    assert anonymous.status_code == 401


@pytest.mark.parametrize("label", ["", "x" * 81, "a\nb", {"x": 1}])
async def test_a_bad_label_is_a_422(harness: Harness, label: Any) -> None:
    async with harness.client() as client:
        response = await client.post(BASE, json={"label": label})

    assert response.status_code == 422
    assert response.json() == {"success": False, "error": "Invalid request"}


async def test_extra_fields_are_refused(harness: Harness) -> None:
    async with harness.client() as client:
        response = await client.post(BASE, json={"label": "x", "key_hash": "y"})

    assert response.status_code == 422


async def test_a_key_looking_label_is_refused_with_its_code(harness: Harness) -> None:
    async with harness.client() as client:
        response = await client.post(BASE, json={"label": "fj_test_abcdefghijklmnop"})

    assert (response.status_code, response.json()["code"]) == (422, "invalid_label")


async def test_a_merchants_writer_without_the_partner_key_permissions_can_neither_list_nor_issue(
    harness: Harness,
) -> None:
    async with harness.client("catalogue") as client:
        listed = await client.get(BASE)
        created = await client.post(BASE, json={"label": "x"})
        revoked = await client.post(f"{BASE}/k/revoke")

    assert (listed.status_code, created.status_code, revoked.status_code) == (
        403,
        403,
        403,
    )
