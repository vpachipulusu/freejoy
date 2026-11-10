"""Admin catalogue routes through the real dependency chain, with fake stores behind them."""

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
from app.api.v1.admin_categories import get_category_service
from app.api.v1.admin_merchants import get_merchant_service
from app.api.v1.admin_offers import get_offer_service
from app.core.firebase import VerifiedIdentity
from app.main import create_app
from app.models.platform.offer import OfferStatus
from app.repositories.admin import AdminGrant
from app.services.admin_categories import AdminCategoryService
from app.services.admin_merchants import AdminMerchantService
from app.services.admin_offers import AdminOfferService
from tests.fakes_admin_catalogue import (
    EARLIER,
    NOW,
    FakeAuditor,
    FakeCategoryStore,
    FakeMerchantStore,
    FakeOfferStore,
    FakeSession,
    category,
    link,
    merchant,
    offer,
)

KEY = "test-only-internal-service-key-not-for-deployment"
CATALOGUE_PERMISSIONS = [
    "offers.read",
    "offers.write",
    "offers.publish",
    "categories.write",
    "merchants.write",
]
OFFER_BODY: dict[str, Any] = {
    "title": "Free Sample Box",
    "summary": "A box",
    "category_id": "cat1",
    "merchant_id": "mer1",
    "offer_type": "FREE_SAMPLE",
    "image_tint": "sage",
    "image_label": "Free",
    "image_alt": "A box",
    "postage_kind": "FREE",
    "eligibility": "UK",
    "availability": "Today",
    "source_url": "https://shop.example.com/box",
}
TOKEN_BODY: dict[str, Any] = {"review_token": "token-from-the-read"}
CATEGORY_BODY = {
    "name": "Home & cleaning",
    "short_name": "Home",
    "description": "Things for the home",
    "intro": "Intro",
    "tint": "sage",
}


APP = create_app()  # built once: creating the app is the slow part


class Harness:
    def __init__(self) -> None:
        self.offers, self.auditor, self.session = (
            FakeOfferStore(),
            FakeAuditor(),
            FakeSession(),
        )
        self.categories = FakeCategoryStore(category())
        self.merchants = FakeMerchantStore(merchant())
        self.grants: dict[str, list[str]] = {}
        self.app = APP
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

        overrides = self.app.dependency_overrides
        overrides[get_session_identity] = identity
        overrides[get_admin_repository] = lambda: Admins()
        overrides[get_session] = lambda: self.session
        overrides[get_offer_service] = lambda: AdminOfferService(
            self.offers, self.session, self.auditor, clock=lambda: NOW
        )
        overrides[get_category_service] = lambda: AdminCategoryService(
            self.categories, self.session, self.auditor
        )
        overrides[get_merchant_service] = lambda: AdminMerchantService(
            self.merchants, self.session, self.auditor
        )
        self.offers.categories = {"cat1": True}

    def client(self, actor: str | None = "editor") -> AsyncClient:
        headers = {"X-Internal-Key": KEY}
        if actor:
            headers["X-Test-Uid"] = actor
        transport = ASGITransport(app=self.app, raise_app_exceptions=False)
        return AsyncClient(transport=transport, base_url="http://test", headers=headers)


async def token_body(client: AsyncClient, offer_id: str = "off1") -> dict[str, Any]:
    """What the admin UI sends: the review token from the offer it loaded."""
    loaded = await client.get(f"/v1/admin/offers/{offer_id}")
    return {"review_token": loaded.json()["data"]["review_token"]}


@pytest.fixture
def harness() -> Iterator[Harness]:
    built = Harness()
    built.grants["editor"] = CATALOGUE_PERMISSIONS
    yield built
    APP.dependency_overrides.clear()


# (method, path, body, permission that route requires)
ROUTES: list[tuple[str, str, dict[str, Any] | None, str]] = [
    ("GET", "/v1/admin/offers", None, "offers.read"),
    ("POST", "/v1/admin/offers", OFFER_BODY, "offers.write"),
    ("GET", "/v1/admin/offers/off1", None, "offers.read"),
    ("PATCH", "/v1/admin/offers/off1", {"title": "x"}, "offers.write"),
    ("POST", "/v1/admin/offers/off1/publish", TOKEN_BODY, "offers.publish"),
    ("POST", "/v1/admin/offers/off1/unpublish", None, "offers.publish"),
    ("POST", "/v1/admin/offers/off1/expire", None, "offers.publish"),
    ("POST", "/v1/admin/offers/off1/archive", None, "offers.publish"),
    ("POST", "/v1/admin/offers/off1/verify", TOKEN_BODY, "offers.publish"),
    (
        "POST",
        "/v1/admin/offers/off1/links",
        {"provider": "awin", "destination_url": "https://www.awin1.com"},
        "offers.write",
    ),
    ("PATCH", "/v1/admin/offers/off1/links/l1", {"active": False}, "offers.write"),
    ("GET", "/v1/admin/categories", None, "offers.read"),
    ("POST", "/v1/admin/categories", CATEGORY_BODY, "categories.write"),
    ("GET", "/v1/admin/categories/cat1", None, "offers.read"),
    ("PATCH", "/v1/admin/categories/cat1", {"name": "x"}, "categories.write"),
    ("POST", "/v1/admin/categories/cat1/enable", None, "categories.write"),
    ("POST", "/v1/admin/categories/cat1/disable", None, "categories.write"),
    ("GET", "/v1/admin/merchants", None, "offers.read"),
    ("POST", "/v1/admin/merchants", {"name": "Acme"}, "merchants.write"),
    ("GET", "/v1/admin/merchants/mer1", None, "offers.read"),
    ("PATCH", "/v1/admin/merchants/mer1", {"name": "x"}, "merchants.write"),
    ("POST", "/v1/admin/merchants/mer1/activate", None, "merchants.write"),
    ("POST", "/v1/admin/merchants/mer1/deactivate", None, "merchants.write"),
]
ROUTE_IDS = [f"{method} {path}" for method, path, _, _ in ROUTES]


@pytest.mark.parametrize(
    ("method", "path", "body", "permission"), ROUTES, ids=ROUTE_IDS
)
async def test_unauthenticated_requests_are_401(
    harness, method, path, body, permission
):
    async with harness.client(None) as client:
        result = await client.request(method, path, json=body)

    assert result.status_code == 401


@pytest.mark.parametrize(
    ("method", "path", "body", "permission"), ROUTES, ids=ROUTE_IDS
)
async def test_a_user_without_an_admin_record_is_403(
    harness, method, path, body, permission
):
    async with harness.client("plain") as client:
        result = await client.request(method, path, json=body)

    assert result.status_code == 403
    assert result.json() == {"success": False, "error": "Forbidden"}
    assert harness.auditor.rows == [] and harness.session.commits == 0


@pytest.mark.parametrize(
    ("method", "path", "body", "permission"), ROUTES, ids=ROUTE_IDS
)
async def test_an_admin_missing_only_the_route_permission_is_403(
    harness, method, path, body, permission
):
    harness.grants["partial"] = [p for p in CATALOGUE_PERMISSIONS if p != permission]
    harness.offers.offers["off1"] = offer("off1")

    async with harness.client("partial") as client:
        result = await client.request(method, path, json=body)

    assert result.status_code == 403
    assert harness.auditor.rows == [] and harness.session.commits == 0
    assert harness.offers.offers["off1"].title == "Offer off1"


@pytest.mark.parametrize(
    ("method", "path", "body", "permission"),
    [row for row in ROUTES if row[0] != "GET"],
    ids=[f"{m} {p}" for m, p, _, _ in ROUTES if m != "GET"],
)
async def test_the_read_permission_alone_never_writes(
    harness, method, path, body, permission
):
    harness.grants["reader"] = ["offers.read"]

    async with harness.client("reader") as client:
        result = await client.request(method, path, json=body)

    assert result.status_code == 403


async def test_create_returns_201_with_the_offer_and_no_store(harness):
    async with harness.client() as client:
        result = await client.post("/v1/admin/offers", json=OFFER_BODY)

    body = result.json()
    assert result.status_code == 201
    assert result.headers["Cache-Control"] == "no-store"
    assert body["success"] is True
    assert (
        body["data"]["slug"] == "free-sample-box" and body["data"]["status"] == "DRAFT"
    )
    assert body["data"]["links"] == [] and body["data"]["last_verified_at"] is None
    assert [row["action"] for row in harness.auditor.rows] == ["admin.offers.create"]


async def test_duplicate_slug_is_409_with_a_stable_code(harness):
    async with harness.client() as client:
        await client.post("/v1/admin/offers", json=OFFER_BODY)
        result = await client.post("/v1/admin/offers", json=OFFER_BODY)

    assert result.status_code == 409
    assert result.json() == {
        "success": False,
        "error": "That slug is already in use",
        "code": "slug_taken",
    }


async def test_unknown_ids_are_404_with_a_code(harness):
    async with harness.client() as client:
        results = [
            await client.get("/v1/admin/offers/ghost"),
            await client.patch("/v1/admin/offers/ghost", json={"title": "x"}),
            await client.post("/v1/admin/offers/ghost/publish", json=TOKEN_BODY),
            await client.post("/v1/admin/offers/ghost/verify", json=TOKEN_BODY),
            await client.get("/v1/admin/categories/ghost"),
            await client.post("/v1/admin/categories/ghost/disable"),
            await client.get("/v1/admin/merchants/ghost"),
            await client.post("/v1/admin/merchants/ghost/deactivate"),
        ]

    assert [r.status_code for r in results] == [404] * 8
    assert all(r.json()["code"] == "not_found" for r in results)
    assert results[0].json()["error"] == "Offer not found"


async def test_publish_blocked_is_422_and_lists_the_reasons(harness):
    harness.offers.offers["off1"] = offer("off1")

    async with harness.client() as client:
        result = await client.post(
            "/v1/admin/offers/off1/publish", json=await token_body(client)
        )

    assert result.status_code == 422
    assert result.json()["code"] == "publish_blocked"
    assert "it has no active affiliate link or source URL" in result.json()["error"]
    assert "it has not been verified" in result.json()["error"]


async def test_verify_then_publish_then_illegal_repeat(harness):
    harness.offers.offers["off1"] = offer("off1", source_url="https://shop.example.com")

    async with harness.client() as client:
        verified = await client.post(
            "/v1/admin/offers/off1/verify",
            json={**await token_body(client), "note": "ok"},
        )
        published = await client.post(
            "/v1/admin/offers/off1/publish", json=await token_body(client)
        )
        again = await client.post(
            "/v1/admin/offers/off1/publish", json=await token_body(client)
        )
        archived = await client.post("/v1/admin/offers/off1/archive")
        revive = await client.post(
            "/v1/admin/offers/off1/publish", json=await token_body(client)
        )

    assert verified.json()["data"]["last_verified_at"] is not None
    assert published.json()["data"]["status"] == "PUBLISHED"
    assert (again.status_code, again.json()["code"]) == (409, "illegal_transition")
    assert archived.json()["data"]["status"] == OfferStatus.ARCHIVED.value
    assert (revive.status_code, revive.json()["code"]) == (409, "illegal_transition")
    assert again.json()["error"] == "Operation not allowed in the current state"


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"source_url": "javascript:alert(1)"}, "invalid_url"),
        ({"source_url": "https://good.example.com@evil.example.com"}, "invalid_url"),
        ({"postage_kind": "COST"}, "invalid_postage"),
        ({"slug": "Not A Slug"}, "invalid_slug"),
        ({"category_id": "ghost"}, "unknown_category"),
        ({"merchant_id": "ghost"}, "unknown_merchant"),
        (
            {"start_at": "2026-02-01T00:00:00Z", "end_at": "2026-01-01T00:00:00Z"},
            "invalid_window",
        ),
    ],
)
async def test_service_level_validation_is_422_with_a_code(harness, changes, code):
    async with harness.client() as client:
        result = await client.post("/v1/admin/offers", json={**OFFER_BODY, **changes})

    assert result.status_code == 422
    assert result.json()["code"] == code
    assert harness.offers.offers == {}


@pytest.mark.parametrize(
    "changes",
    [
        {"title": ""},
        {"title": "line\nbreak"},
        {"title": "nul\x00byte"},
        {"title": "x" * 201},
        {"offer_type": "GIFT"},
        {"image_tint": "Not A Tint"},
        {"postage_currency": "gbp"},
        {"postage_amount_minor": -1},
        {"start_at": "2026-01-01T00:00:00"},
        {"surprise": "field"},
        {"status": "PUBLISHED"},
        {"id": "chosen-by-client"},
        {"description": ["x"] * 21},
        {"steps": [{"title": "a"}]},
        {"source_url": "x" * 2049},
    ],
)
async def test_schema_validation_is_422_with_the_standard_envelope(harness, changes):
    async with harness.client() as client:
        result = await client.post("/v1/admin/offers", json={**OFFER_BODY, **changes})

    assert result.status_code == 422
    assert result.json() == {"success": False, "error": "Invalid request"}
    assert harness.offers.offers == {}


async def test_paragraph_fields_allow_line_breaks(harness):
    body = {**OFFER_BODY, "description": ["One\nTwo"], "eligibility": "UK\nonly"}

    async with harness.client() as client:
        result = await client.post("/v1/admin/offers", json=body)

    assert result.status_code == 201


@pytest.mark.parametrize(
    "body",
    [{}, {"title": None}, {"category_id": None}, {"slug": "x"}, {"status": "DRAFT"}],
)
async def test_patch_rejects_empty_null_and_forbidden_fields(harness, body):
    harness.offers.offers["off1"] = offer("off1")

    async with harness.client() as client:
        result = await client.patch("/v1/admin/offers/off1", json=body)

    assert result.status_code == 422
    assert harness.auditor.rows == []


async def test_patch_may_clear_nullable_fields(harness):
    harness.offers.offers["off1"] = offer("off1", source_url="https://a.example.com")

    async with harness.client() as client:
        result = await client.patch("/v1/admin/offers/off1", json={"source_url": None})

    assert result.status_code == 200
    assert result.json()["data"]["source_url"] is None


async def test_list_passes_filters_and_returns_a_page(harness):
    harness.offers.offers["off1"] = offer("off1")

    async with harness.client() as client:
        result = await client.get(
            "/v1/admin/offers?search=box&status=DRAFT&category_id=cat1&limit=5&offset=0"
        )
        empty = await client.get("/v1/admin/offers?status=ARCHIVED")
        bad = await client.get("/v1/admin/offers?status=nope")
        too_many = await client.get("/v1/admin/offers?limit=101")

    filters = harness.offers.filters[0][0]
    assert (filters.search, filters.status, filters.category_id) == (
        "box",
        OfferStatus.DRAFT,
        "cat1",
    )
    assert result.json()["data"]["total_count"] == 1
    assert result.json()["data"]["offers"][0]["slug"] == "off1"
    assert empty.status_code == 200
    assert (bad.status_code, too_many.status_code) == (422, 422)


async def test_list_of_nothing_is_an_empty_page(harness):
    async with harness.client() as client:
        result = await client.get("/v1/admin/offers")

    assert result.json()["data"] == {
        "offers": [],
        "total_count": 0,
        "limit": 20,
        "offset": 0,
    }


async def test_link_routes_create_update_and_report_missing_links(harness):
    harness.offers.offers["off1"] = offer("off1")
    harness.offers.links["l1"] = link("l1")

    async with harness.client() as client:
        created = await client.post(
            "/v1/admin/offers/off1/links",
            json={"provider": "awin", "destination_url": "https://www.awin1.com/x"},
        )
        updated = await client.patch(
            "/v1/admin/offers/off1/links/l1",
            json={"tracking_id": None, "active": False},
        )
        missing = await client.patch(
            "/v1/admin/offers/off1/links/zzz", json={"active": False}
        )
        unsafe = await client.post(
            "/v1/admin/offers/off1/links",
            json={"provider": "awin", "destination_url": "http://www.awin1.com"},
        )
        bad_provider = await client.post(
            "/v1/admin/offers/off1/links",
            json={"provider": "AWIN!", "destination_url": "https://www.awin1.com"},
        )
        empty_patch = await client.patch("/v1/admin/offers/off1/links/l1", json={})
        null_url = await client.patch(
            "/v1/admin/offers/off1/links/l1", json={"destination_url": None}
        )

    assert created.status_code == 201 and created.json()["data"]["active"] is True
    assert updated.json()["data"]["active"] is False
    assert (missing.status_code, missing.json()["code"]) == (404, "not_found")
    assert (unsafe.status_code, unsafe.json()["code"]) == (422, "invalid_url")
    assert (
        bad_provider.status_code,
        empty_patch.status_code,
        null_url.status_code,
    ) == (
        422,
        422,
        422,
    )


async def test_category_routes_round_trip(harness):
    async with harness.client() as client:
        created = await client.post("/v1/admin/categories", json=CATEGORY_BODY)
        duplicate = await client.post("/v1/admin/categories", json=CATEGORY_BODY)
        listing = await client.get("/v1/admin/categories?enabled=true")
        updated = await client.patch(
            f"/v1/admin/categories/{created.json()['data']['id']}",
            json={"sort_order": 3},
        )
        disabled = await client.post(
            f"/v1/admin/categories/{created.json()['data']['id']}/disable"
        )
        again = await client.post(
            f"/v1/admin/categories/{created.json()['data']['id']}/disable"
        )
        enabled = await client.post(
            f"/v1/admin/categories/{created.json()['data']['id']}/enable"
        )
        empty = await client.patch("/v1/admin/categories/cat1", json={})
        null = await client.patch("/v1/admin/categories/cat1", json={"name": None})

    assert created.status_code == 201
    assert created.json()["data"]["slug"] == "home-cleaning"
    assert duplicate.status_code == 409 and duplicate.json()["code"] == "slug_taken"
    assert listing.json()["data"]["total_count"] == 2
    assert updated.json()["data"]["sort_order"] == 3
    assert disabled.json()["data"]["enabled"] is False and again.status_code == 200
    assert enabled.json()["data"]["enabled"] is True
    assert (empty.status_code, null.status_code) == (422, 422)
    assert [r["action"] for r in harness.auditor.rows] == [
        "admin.categories.create",
        "admin.categories.update",
        "admin.categories.disable",
        "admin.categories.enable",
    ]


async def test_merchant_routes_round_trip(harness):
    async with harness.client() as client:
        created = await client.post(
            "/v1/admin/merchants",
            json={"name": "Acme Ltd", "website": "https://Acme.example.com"},
        )
        unsafe = await client.post(
            "/v1/admin/merchants",
            json={"name": "Evil", "logo_url": "javascript:alert(1)"},
        )
        merchant_id = created.json()["data"]["id"]
        updated = await client.patch(
            f"/v1/admin/merchants/{merchant_id}",
            json={"logo_url": "https://cdn.example.com/a.png"},
        )
        off = await client.post(f"/v1/admin/merchants/{merchant_id}/deactivate")
        on = await client.post(f"/v1/admin/merchants/{merchant_id}/activate")
        listing = await client.get("/v1/admin/merchants?active=true&search=acme")
        empty = await client.patch(f"/v1/admin/merchants/{merchant_id}", json={})
        null = await client.patch(
            f"/v1/admin/merchants/{merchant_id}", json={"name": None}
        )

    assert created.json()["data"]["website"] == "https://acme.example.com"
    assert (unsafe.status_code, unsafe.json()["code"]) == (422, "invalid_url")
    assert updated.json()["data"]["logo_url"] == "https://cdn.example.com/a.png"
    assert off.json()["data"]["active"] is False and on.json()["data"]["active"] is True
    assert listing.json()["data"]["total_count"] == 2
    assert (empty.status_code, null.status_code) == (422, 422)


async def test_audit_and_verified_timestamps_use_the_injected_clock(harness):
    harness.offers.offers["off1"] = offer("off1", last_verified_at=EARLIER)

    async with harness.client() as client:
        result = await client.post(
            "/v1/admin/offers/off1/verify", json=await token_body(client)
        )

    assert result.json()["data"]["last_verified_at"].startswith("2026-06-01T12:00:00")


@pytest.mark.parametrize(
    ("method", "path", "body", "permission"),
    [row for row in ROUTES if row[0] in ("POST", "PATCH") and row[2] is not None],
    ids=[
        f"{m} {p}" for m, p, b, _ in ROUTES if m in ("POST", "PATCH") and b is not None
    ],
)
async def test_a_partial_admin_sending_an_invalid_body_gets_403_not_422(
    harness, method, path, body, permission
):
    harness.grants["partial"] = [p for p in CATALOGUE_PERMISSIONS if p != permission]

    async with harness.client("partial") as client:
        result = await client.request(method, path, json={"title": 5, "bogus": 1})

    assert result.status_code == 403
    assert result.json() == {"success": False, "error": "Forbidden"}


# --- the review token ---------------------------------------------------------------------------


async def test_the_detail_returns_an_opaque_review_token_and_the_list_does_not(harness):
    harness.offers.offers["off1"] = offer("off1")
    harness.offers.links["l1"] = link("l1")

    async with harness.client() as client:
        detail = (await client.get("/v1/admin/offers/off1")).json()["data"]
        listed = (await client.get("/v1/admin/offers")).json()["data"]["offers"][0]

    assert len(detail["review_token"]) == 64
    assert "review_token" not in listed


async def test_publish_and_verify_without_a_token_are_422_and_change_nothing(harness):
    harness.offers.offers["off1"] = offer(
        "off1", last_verified_at=EARLIER, source_url="https://shop.example.com"
    )

    async with harness.client() as client:
        results = [
            await client.post("/v1/admin/offers/off1/publish"),
            await client.post("/v1/admin/offers/off1/publish", json={}),
            await client.post("/v1/admin/offers/off1/verify", json={"note": "ok"}),
            await client.post(
                "/v1/admin/offers/off1/verify", json={"review_token": ""}
            ),
        ]

    assert [r.status_code for r in results] == [422] * 4
    assert harness.offers.offers["off1"].status is OfferStatus.DRAFT
    assert harness.auditor.rows == [] and harness.session.commits == 0


@pytest.mark.parametrize("step", ["publish", "verify"])
async def test_a_stale_token_is_409_offer_changed_and_nothing_is_written(harness, step):
    harness.offers.offers["off1"] = offer(
        "off1", last_verified_at=EARLIER, source_url="https://shop.example.com"
    )
    harness.offers.links["l1"] = link("l1")

    async with harness.client() as client:
        stale = await token_body(client)
        harness.offers.links["l1"] = replace(
            harness.offers.links["l1"], destination_url="https://shop.example.com/new"
        )
        result = await client.post(f"/v1/admin/offers/off1/{step}", json=stale)

    assert result.status_code == 409
    assert result.json() == {
        "success": False,
        "error": "The offer changed after it was loaded: review it again",
        "code": "offer_changed",
    }
    assert harness.offers.offers["off1"].status is OfferStatus.DRAFT
    assert harness.auditor.rows == [] and harness.session.commits == 0


async def test_the_response_of_a_verify_carries_the_token_the_next_publish_needs(
    harness,
):
    harness.offers.offers["off1"] = offer("off1", source_url="https://shop.example.com")

    async with harness.client() as client:
        verified = await client.post(
            "/v1/admin/offers/off1/verify", json=await token_body(client)
        )
        published = await client.post(
            "/v1/admin/offers/off1/publish",
            json={"review_token": verified.json()["data"]["review_token"]},
        )

    assert published.status_code == 200
    assert published.json()["data"]["status"] == "PUBLISHED"


@pytest.mark.parametrize("step", ["unpublish", "expire", "archive"])
async def test_taking_an_offer_off_the_site_needs_no_token(harness, step):
    harness.offers.offers["off1"] = offer(
        "off1",
        status=OfferStatus.PUBLISHED,
        last_verified_at=EARLIER,
        source_url="https://shop.example.com",
    )

    async with harness.client() as client:
        result = await client.post(f"/v1/admin/offers/off1/{step}")

    assert result.status_code == 200
