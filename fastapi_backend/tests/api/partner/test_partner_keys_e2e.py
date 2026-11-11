"""End to end on real Postgres: issue a key as an admin, use it as a partner, revoke it (ADR-013, FREEJ-57).

The admin side runs as the owner (the normal internal API); the partner side runs through the real
``PartnerSessionProvider`` on the restricted role, applied from ``commands/sql/partner_role.sql`` (as in
``tests/core/test_partner_role_e2e.py``; the role is ``freejoy_partner_<database>``, never the production name).
"""

from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine

import tests.catalogue_e2e_support as support
from app.core.config import settings
from app.core.database import PartnerSessionProvider
from app.core.partner_keys import key_digest
from app.main import app
from app.partner import deps
from tests.api.partner.support import call_raw
from tests.catalogue_e2e_support import as_user, rows, seed_admin
from tests.partner_role_support import (
    apply_partner_role,
    role_name_for,
    role_password_for,
    role_url,
)

pytestmark = support.requires_database
database_url, engine, client = support.database_url, support.engine, support.client

MERCHANTS = "/v1/admin/merchants"
ME = "/partner/v1/me"


@pytest.fixture
async def partner_world(
    database_url: str, engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[None]:
    role = role_name_for(make_url(database_url).database or "")
    await apply_partner_role(database_url, role, role_password_for(role))
    provider = PartnerSessionProvider(
        role_url(database_url, role, role_password_for(role))
    )
    monkeypatch.setattr(deps, "get_partner_session_provider", lambda: provider)
    monkeypatch.setattr(settings, "PARTNER_API_ENABLED", True)
    yield


@pytest.fixture
async def admin(engine: AsyncEngine) -> dict[str, str]:
    await seed_admin(
        engine,
        "keyadmin",
        ["merchants.write", "partner_keys.read", "partner_keys.write"],
    )
    return as_user("keyadmin")


async def issue_key(client: AsyncClient, admin: dict[str, str]) -> tuple[str, str, str]:
    """Create a merchant and a key; returns (merchant id, key id, the key)."""
    merchant = await client.post(
        MERCHANTS,
        json={"name": "Shop", "website": "https://shop.example.com"},
        headers=admin,
    )
    merchant_id = merchant.json()["data"]["id"]
    created = await client.post(
        f"{MERCHANTS}/{merchant_id}/partner-keys",
        json={"label": "Production feed"},
        headers=admin,
    )
    data = created.json()["data"]
    return merchant_id, data["key"]["id"], data["api_key"]


async def call_me(key: str) -> tuple[int, dict[str, object]]:
    response = await call_raw(
        app, "GET", ME, headers=[("authorization", f"Bearer {key}")]
    )
    return response.status, response.json()


async def test_a_new_key_calls_me_and_gets_its_merchant_and_label(
    client: AsyncClient, admin: dict[str, str], partner_world: None
) -> None:
    _, _, key = await issue_key(client, admin)

    status, body = await call_me(key)

    assert status == 200
    assert body == {
        "success": True,
        "data": {"merchant_name": "Shop", "key_label": "Production feed"},
    }


async def test_only_the_hash_of_the_key_is_at_rest(
    client: AsyncClient, engine: AsyncEngine, admin: dict[str, str], partner_world: None
) -> None:
    _, key_id, key = await issue_key(client, admin)

    (stored,) = await rows(
        engine, "SELECT key_hash FROM partner_api_keys WHERE id = :i", i=key_id
    )

    assert stored.key_hash == key_digest(key)
    assert key not in stored.key_hash


async def test_last_used_at_moves_on_use_and_at_most_once_a_minute(
    client: AsyncClient, engine: AsyncEngine, admin: dict[str, str], partner_world: None
) -> None:
    _, key_id, key = await issue_key(client, admin)
    sql = "SELECT last_used_at FROM partner_api_keys WHERE id = :i"
    (before,) = await rows(engine, sql, i=key_id)

    await call_me(key)
    (first,) = await rows(engine, sql, i=key_id)
    await call_me(key)
    (again,) = await rows(engine, sql, i=key_id)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE partner_api_keys SET last_used_at = now() - interval '5 minutes' WHERE id = :i"
            ),
            {"i": key_id},
        )
    await call_me(key)
    (later,) = await rows(engine, sql, i=key_id)

    assert before.last_used_at is None
    assert first.last_used_at is not None
    assert (
        again.last_used_at == first.last_used_at
    )  # inside the minute: not written again
    assert later.last_used_at is not None
    assert later.last_used_at > first.last_used_at - timedelta(minutes=1)


async def test_revoking_takes_effect_on_the_next_request(
    client: AsyncClient, admin: dict[str, str], partner_world: None
) -> None:
    merchant_id, key_id, key = await issue_key(client, admin)
    before, _ = await call_me(key)

    revoked = await client.post(
        f"{MERCHANTS}/{merchant_id}/partner-keys/{key_id}/revoke", headers=admin
    )
    after, body = await call_me(key)

    assert revoked.status_code == 200
    assert (before, after) == (200, 401)
    assert body == {"success": False, "error": "Unauthorized"}


async def test_deactivating_the_merchant_stops_its_keys(
    client: AsyncClient, admin: dict[str, str], partner_world: None
) -> None:
    merchant_id, _, key = await issue_key(client, admin)

    await client.post(f"{MERCHANTS}/{merchant_id}/deactivate", headers=admin)
    status, _ = await call_me(key)

    assert status == 401


async def test_a_key_from_another_environment_or_a_wrong_secret_is_refused(
    client: AsyncClient, admin: dict[str, str], partner_world: None
) -> None:
    _, _, key = await issue_key(client, admin)
    wrong = key[:-1] + ("0" if key[-1] != "0" else "1")

    assert (await call_me(wrong))[0] == 401
    assert (await call_me(key.replace("_test_", "_production_", 1)))[0] == 401


async def test_one_merchants_key_is_never_another_merchants(
    client: AsyncClient, admin: dict[str, str], partner_world: None
) -> None:
    _, _, first_key = await issue_key(client, admin)
    other = await client.post(
        MERCHANTS,
        json={"name": "Other", "website": "https://other.example.com"},
        headers=admin,
    )
    other_id = other.json()["data"]["id"]
    created = await client.post(
        f"{MERCHANTS}/{other_id}/partner-keys", json={"label": "Theirs"}, headers=admin
    )
    other_key = created.json()["data"]["api_key"]

    _, first = await call_me(first_key)
    _, second = await call_me(other_key)

    assert first["data"] == {"merchant_name": "Shop", "key_label": "Production feed"}  # type: ignore[index]
    assert second["data"] == {"merchant_name": "Other", "key_label": "Theirs"}  # type: ignore[index]


async def test_a_partner_surface_wired_to_the_owner_url_answers_503_even_with_a_valid_key(
    client: AsyncClient,
    admin: dict[str, str],
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _, _, key = await issue_key(client, admin)
    monkeypatch.setattr(settings, "PARTNER_API_ENABLED", True)
    monkeypatch.setattr(
        deps,
        "get_partner_session_provider",
        lambda: PartnerSessionProvider(database_url),
    )
    caplog.set_level("ERROR")

    status, body = await call_me(key)

    assert status == 503
    assert body == {"success": False, "error": "Service temporarily unavailable"}
    assert database_url not in caplog.text
    assert key not in caplog.text
