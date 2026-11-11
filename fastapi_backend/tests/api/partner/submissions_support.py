"""Shared setup of the partner submission end-to-end suites: a real database, the real restricted role, the real app.

The admin side runs as the owner through the normal internal API (``tests/catalogue_e2e_support``); the partner
side goes through the mounted partner sub-app and ``PartnerSessionProvider`` on the role applied from
``commands/sql/partner_role.sql`` (``freejoy_partner_<database>``, never the production name).
"""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine

import tests.catalogue_e2e_support as support
from app.core.config import settings
from app.core.database import PartnerSessionProvider
from app.main import app
from app.partner import deps
from tests.api.partner.support import RawResponse, call_raw
from tests.catalogue_e2e_support import CATEGORY_BODY, as_user, seed_admin
from tests.partner_role_support import (
    apply_partner_role,
    role_name_for,
    role_password_for,
    role_url,
)

MERCHANTS = "/v1/admin/merchants"
OFFERS = "/v1/admin/offers"
PARTNER_OFFERS = "/partner/v1/offers"
SITE = "https://shop.example.com"
ADMIN_PERMISSIONS = [
    "offers.read",
    "offers.write",
    "offers.publish",
    "categories.write",
    "merchants.write",
    "partner_keys.read",
    "partner_keys.write",
    "audit.read",
]


@pytest.fixture(scope="module")
def partner_role(database_url: str) -> str:
    """The restricted role, applied once per module from the committed script."""
    role = role_name_for(make_url(database_url).database or "")
    asyncio.run(apply_partner_role(database_url, role, role_password_for(role)))
    return role


@pytest.fixture
async def partner_world(
    database_url: str,
    partner_role: str,
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[None]:
    provider = PartnerSessionProvider(
        role_url(database_url, partner_role, role_password_for(partner_role))
    )
    monkeypatch.setattr(deps, "get_partner_session_provider", lambda: provider)
    monkeypatch.setattr(settings, "PARTNER_API_ENABLED", True)
    yield


@pytest.fixture
async def admin(engine: AsyncEngine) -> dict[str, str]:
    await seed_admin(engine, "editor", ADMIN_PERMISSIONS)
    return as_user("editor")


@dataclass(frozen=True)
class Merchant:
    id: str
    key_id: str
    key: str
    website: str


async def make_category(client: AsyncClient, admin: dict[str, str]) -> str:
    """An enabled category; returns its slug (what a retailer names)."""
    response = await client.post(
        "/v1/admin/categories", json=CATEGORY_BODY, headers=admin
    )
    return str(response.json()["data"]["slug"])


async def make_merchant(
    client: AsyncClient,
    admin: dict[str, str],
    name: str = "Shop",
    website: str = SITE,
) -> Merchant:
    created = await client.post(
        MERCHANTS, json={"name": name, "website": website}, headers=admin
    )
    merchant_id = created.json()["data"]["id"]
    key = await client.post(
        f"{MERCHANTS}/{merchant_id}/partner-keys", json={"label": "Feed"}, headers=admin
    )
    data = key.json()["data"]
    return Merchant(merchant_id, data["key"]["id"], data["api_key"], website)


def offer_body(category: str, /, **changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "title": "Free sample box",
        "summary": "A box of samples, free",
        "description": ["Try our range at no cost."],
        "offer_type": "FREE_SAMPLE",
        "category": category,
        "url": f"{SITE}/free-sample",
        "postage_kind": "FREE",
        "requires_card": False,
        "subscription_terms": None,
        "eligibility": "UK residents aged 18 or over",
        "availability": "While stocks last",
        "steps": [{"title": "Fill in the form", "detail": "Give your address"}],
        "start_at": None,
        "end_at": None,
    }
    return {**body, **changes}


async def partner_call(
    key: str, method: str, path: str, body: Any = None, *, raw: bytes | None = None
) -> RawResponse:
    headers = [("authorization", f"Bearer {key}")]
    chunks = (b"",)
    if body is not None or raw is not None:
        headers.append(("content-type", "application/json"))
        chunks = (raw if raw is not None else json.dumps(body).encode(),)
    path, _, query = path.partition("?")
    return await call_raw(
        app, method, path, headers=headers, chunks=chunks, query_string=query
    )


async def put_offer(key: str, ref: str, body: dict[str, Any]) -> RawResponse:
    return await partner_call(key, "PUT", f"{PARTNER_OFFERS}/{ref}", body)


async def put_many(
    key: str, jobs: list[tuple[str, dict[str, Any]]]
) -> list[RawResponse]:
    return list(
        await asyncio.gather(*(put_offer(key, ref, body) for ref, body in jobs))
    )


async def offer_id_of(client: AsyncClient, admin: dict[str, str], title: str) -> str:
    listed = await client.get(f"{OFFERS}?search={title}", headers=admin)
    return str(listed.json()["data"]["offers"][0]["id"])


__all__ = [
    "CATEGORY_BODY",
    "support",
]
