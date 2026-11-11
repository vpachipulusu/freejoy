"""End to end: the admin partner key API on real Postgres (create shows the key once, hash only at rest)."""

import json

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

import tests.catalogue_e2e_support as support
from app.core.partner_keys import key_digest
from tests.catalogue_e2e_support import as_user, audit_rows, rows, seed_admin

pytestmark = support.requires_database
database_url, engine, client = support.database_url, support.engine, support.client

MERCHANTS = "/v1/admin/merchants"
WEBSITE = "https://shop.example.com"


@pytest.fixture
async def admin(engine: AsyncEngine) -> dict[str, str]:
    await seed_admin(
        engine,
        "keyadmin",
        ["merchants.write", "partner_keys.read", "partner_keys.write"],
    )
    return as_user("keyadmin")


async def merchant_with_website(client: AsyncClient, admin: dict[str, str]) -> str:
    created = await client.post(
        MERCHANTS, json={"name": "Shop", "website": WEBSITE}, headers=admin
    )
    return str(created.json()["data"]["id"])


async def issue(
    client: AsyncClient, admin: dict[str, str], merchant_id: str, label: str = "Feed"
) -> dict[str, object]:
    response = await client.post(
        f"{MERCHANTS}/{merchant_id}/partner-keys", json={"label": label}, headers=admin
    )
    assert response.status_code == 201, response.text
    data: dict[str, object] = response.json()["data"]
    return data


async def test_create_returns_the_key_once_and_the_database_keeps_only_its_hash(
    client: AsyncClient, engine: AsyncEngine, admin: dict[str, str]
) -> None:
    merchant_id = await merchant_with_website(client, admin)

    issued = await issue(client, admin, merchant_id)

    api_key = str(issued["api_key"])
    key = issued["key"]
    assert isinstance(key, dict)
    assert api_key.startswith(f"fj_test_{key['id']}_")
    stored = await rows(
        engine, "SELECT id, key_hash, label, created_by FROM partner_api_keys"
    )
    assert len(stored) == 1
    assert stored[0].key_hash == key_digest(api_key)
    everything = json.dumps(
        [list(r) for r in await rows(engine, "SELECT * FROM partner_api_keys")],
        default=str,
    )
    assert api_key not in everything and api_key.rsplit("_", 1)[1] not in everything


async def test_listing_never_shows_the_key_or_its_hash(
    client: AsyncClient, admin: dict[str, str]
) -> None:
    merchant_id = await merchant_with_website(client, admin)
    issued = await issue(client, admin, merchant_id)

    listed = await client.get(f"{MERCHANTS}/{merchant_id}/partner-keys", headers=admin)

    body = listed.text
    assert listed.status_code == 200
    assert listed.headers["cache-control"] == "no-store"
    assert str(issued["api_key"]) not in body
    assert key_digest(str(issued["api_key"])) not in body
    (item,) = listed.json()["data"]
    assert set(item) == {
        "id",
        "merchant_id",
        "label",
        "created_at",
        "last_used_at",
        "revoked_at",
    }


async def test_create_and_revoke_are_audited_without_the_key(
    client: AsyncClient, engine: AsyncEngine, admin: dict[str, str]
) -> None:
    merchant_id = await merchant_with_website(client, admin)
    issued = await issue(client, admin, merchant_id)
    key = issued["key"]
    assert isinstance(key, dict)

    revoked = await client.post(
        f"{MERCHANTS}/{merchant_id}/partner-keys/{key['id']}/revoke", headers=admin
    )
    again = await client.post(
        f"{MERCHANTS}/{merchant_id}/partner-keys/{key['id']}/revoke", headers=admin
    )

    assert revoked.status_code == again.status_code == 200
    assert revoked.json()["data"]["revoked_at"] is not None
    audited = [r for r in await audit_rows(engine) if r[2] == "partner_api_keys"]
    assert [r[1] for r in audited] == [
        "admin.partner_keys.create",
        "admin.partner_keys.revoke",
    ]
    assert all(r[3] == key["id"] for r in audited)
    dump = json.dumps([list(r) for r in audited], default=str)
    assert str(issued["api_key"]) not in dump
    assert key_digest(str(issued["api_key"])) not in dump


async def test_a_merchant_without_a_website_or_an_inactive_one_gets_no_key(
    client: AsyncClient, engine: AsyncEngine, admin: dict[str, str]
) -> None:
    bare = await client.post(MERCHANTS, json={"name": "Bare"}, headers=admin)
    inactive = await client.post(
        MERCHANTS,
        json={"name": "Dormant", "website": WEBSITE, "active": False},
        headers=admin,
    )

    no_site = await client.post(
        f"{MERCHANTS}/{bare.json()['data']['id']}/partner-keys",
        json={"label": "x"},
        headers=admin,
    )
    dormant = await client.post(
        f"{MERCHANTS}/{inactive.json()['data']['id']}/partner-keys",
        json={"label": "x"},
        headers=admin,
    )

    assert (no_site.status_code, no_site.json()["code"]) == (409, "merchant_no_website")
    assert (dormant.status_code, dormant.json()["code"]) == (409, "merchant_inactive")
    assert await rows(engine, "SELECT 1 FROM partner_api_keys") == []


async def test_a_key_cannot_be_revoked_through_another_merchant(
    client: AsyncClient, admin: dict[str, str]
) -> None:
    first = await merchant_with_website(client, admin)
    other = await client.post(
        MERCHANTS, json={"name": "Other", "website": WEBSITE}, headers=admin
    )
    issued = await issue(client, admin, first)
    key = issued["key"]
    assert isinstance(key, dict)

    response = await client.post(
        f"{MERCHANTS}/{other.json()['data']['id']}/partner-keys/{key['id']}/revoke",
        headers=admin,
    )

    assert response.status_code == 404


async def test_reading_needs_partner_keys_read_and_changing_needs_partner_keys_write(
    client: AsyncClient, engine: AsyncEngine, admin: dict[str, str]
) -> None:
    merchant_id = await merchant_with_website(client, admin)
    await seed_admin(engine, "reader", ["partner_keys.read"])
    await seed_admin(engine, "writer", ["partner_keys.write"])
    await seed_admin(engine, "catalogue", ["merchants.write"])
    url = f"{MERCHANTS}/{merchant_id}/partner-keys"

    reader_list = await client.get(url, headers=as_user("reader"))
    reader_create = await client.post(
        url, json={"label": "x"}, headers=as_user("reader")
    )
    writer_list = await client.get(url, headers=as_user("writer"))
    nobody = await client.get(url)

    catalogue_create = await client.post(
        url, json={"label": "x"}, headers=as_user("catalogue")
    )
    catalogue_list = await client.get(url, headers=as_user("catalogue"))

    assert catalogue_create.status_code == catalogue_list.status_code == 403
    assert reader_list.status_code == 200
    assert reader_create.status_code == 403
    assert writer_list.status_code == 403
    assert nobody.status_code == 401


async def test_the_key_never_reaches_the_admin_logs(
    client: AsyncClient,
    admin: dict[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("DEBUG")
    merchant_id = await merchant_with_website(client, admin)

    issued = await issue(client, admin, merchant_id)

    assert str(issued["api_key"]) not in caplog.text
    assert str(issued["api_key"]).rsplit("_", 1)[1] not in caplog.text


@pytest.mark.parametrize("label", ["", " ", "x" * 81, "line\nbreak"])
async def test_a_bad_label_is_a_validation_error(
    client: AsyncClient, admin: dict[str, str], label: str
) -> None:
    merchant_id = await merchant_with_website(client, admin)

    response = await client.post(
        f"{MERCHANTS}/{merchant_id}/partner-keys", json={"label": label}, headers=admin
    )

    assert response.status_code == 422
