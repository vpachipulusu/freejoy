"""GET /v1/admin/analytics/clicks through the real dependency chain, with a fake report service."""

import time
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Annotated, Any

import pytest
from fastapi import Header, HTTPException
from httpx import ASGITransport, AsyncClient

from app.api.admin import get_admin_repository
from app.api.auth import get_session_identity
from app.api.deps import get_session
from app.api.v1.admin_analytics import get_click_report_service
from app.core.firebase import VerifiedIdentity
from app.main import create_app
from app.repositories.admin import AdminGrant
from app.repositories.merchant_clicks import (
    DayClicks,
    MerchantClickTotals,
    MerchantRef,
    OfferClicks,
    OfferClicksPage,
)
from app.services.merchant_click_report import MerchantClickReportService

KEY = "test-only-internal-service-key-not-for-deployment"
PATH = "/v1/admin/analytics/clicks"
NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
ANALYTICS_ONLY = ["users.read", "subscriptions.read", "settings.read"]
QUERY = {"merchant_id": "m1", "from": "2026-03-10", "to": "2026-03-11"}

APP = create_app()  # built once: creating the app is the slow part


class FakeStore:
    def __init__(self, merchant: MerchantRef | None) -> None:
        self.merchant = merchant
        self.seen: list[Any] = []

    async def get_merchant(self, merchant_id: str) -> MerchantRef | None:
        self.seen.append(merchant_id)
        return self.merchant

    async def totals(self, merchant_id, start, end):  # type: ignore[no-untyped-def]
        return MerchantClickTotals(3, 2)

    async def offers(self, merchant_id, start, end, limit):  # type: ignore[no-untyped-def]
        return OfferClicksPage(
            [
                OfferClicks("o1", "soap", "Soap", 3),
                OfferClicks("o2", "balm", "Balm", 0),
            ],
            2,
        )

    async def daily(self, offer_ids, start, end):  # type: ignore[no-untyped-def]
        return [
            DayClicks("o1", date(2026, 3, 10), 2),
            DayClicks("o1", date(2026, 3, 11), 1),
        ]


class Harness:
    def __init__(self, merchant: MerchantRef | None) -> None:
        self.store = FakeStore(merchant)
        self.grants: dict[str, list[str]] = {"owner": ["offers.read"]}
        grants = self.grants

        class Admins:
            async def get_grant(self, uid: str) -> AdminGrant | None:
                name = uid.removeprefix("uid-")
                return (
                    AdminGrant(name, "admin", grants[name]) if name in grants else None
                )

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
        overrides[get_session] = lambda: None
        overrides[get_click_report_service] = lambda: MerchantClickReportService(
            self.store, clock=lambda: NOW
        )

    def client(self, actor: str | None = "owner", key: str | None = KEY) -> AsyncClient:
        headers = {}
        if key:
            headers["X-Internal-Key"] = key
        if actor:
            headers["X-Test-Uid"] = actor
        transport = ASGITransport(app=APP, raise_app_exceptions=False)
        return AsyncClient(transport=transport, base_url="http://test", headers=headers)


@pytest.fixture
def harness() -> Iterator[Harness]:
    yield Harness(MerchantRef("m1", "Acme", "acme"))
    APP.dependency_overrides.clear()


async def test_an_admin_with_offers_read_gets_the_report_and_it_is_never_cached(
    harness,
):
    async with harness.client() as client:
        result = await client.get(PATH, params=QUERY)

    assert result.status_code == 200
    assert result.headers["cache-control"] == "no-store"
    data = result.json()["data"]
    assert result.json()["success"] is True
    assert data["merchant"] == {"id": "m1", "name": "Acme", "slug": "acme"}
    assert (data["from_date"], data["to_date"]) == ("2026-03-10", "2026-03-11")
    assert (data["total_clicks"], data["excluded_bot_clicks"]) == (3, 2)
    assert data["offers_total"] == 2
    assert data["max_offers"] == 100
    assert data["offers_truncated"] is False
    assert data["offers"][0] == {
        "offer_id": "o1",
        "slug": "soap",
        "title": "Soap",
        "total_clicks": 3,
        "days": [
            {"date": "2026-03-10", "clicks": 2},
            {"date": "2026-03-11", "clicks": 1},
        ],
    }
    assert data["offers"][1]["days"] == [] and data["offers"][1]["total_clicks"] == 0


async def test_the_report_states_what_a_click_is_and_the_retention_limit(harness):
    async with harness.client() as client:
        data = (await client.get(PATH, params=QUERY)).json()["data"]

    assert "not a sale" in data["basis"]["definition"]
    assert "unique" in data["basis"]["definition"]
    assert "BOT" in data["basis"]["bot_rule"]
    assert data["retention"] == {
        "days": 395,
        "earliest_complete_date": "2025-09-18",
        "starts_before_retention": False,
    }


async def test_the_response_carries_counts_only(harness):
    async with harness.client() as client:
        result = await client.get(PATH, params=QUERY)

    body = result.text.lower()
    for forbidden in (
        "referrer",
        "user_agent",
        "user agent",
        "ip_address",
        "affiliate",
    ):
        assert forbidden not in body.replace("user_agent_class", "")


async def test_a_range_before_retention_is_flagged(harness):
    async with harness.client() as client:
        result = await client.get(
            PATH, params={**QUERY, "from": "2025-01-01", "to": "2025-01-31"}
        )

    assert result.json()["data"]["retention"]["starts_before_retention"] is True


async def test_an_unknown_merchant_is_a_404_with_a_stable_code(harness):
    harness.store.merchant = None

    async with harness.client() as client:
        result = await client.get(PATH, params={**QUERY, "merchant_id": "nope"})

    assert result.status_code == 404
    assert result.json() == {
        "success": False,
        "error": "Merchant not found",
        "code": "not_found",
    }
    assert result.headers["cache-control"] == "no-store"


async def test_unauthenticated_requests_are_401(harness):
    async with harness.client(actor=None) as client:
        result = await client.get(PATH, params=QUERY)

    assert result.status_code == 401
    assert harness.store.seen == []


async def test_a_request_without_the_internal_key_is_401_before_anything_runs(harness):
    async with harness.client(key=None) as client:
        result = await client.get(PATH, params=QUERY)

    assert result.status_code == 401
    assert harness.store.seen == []


async def test_a_signed_in_user_without_an_admin_record_is_403(harness):
    async with harness.client("plain") as client:
        result = await client.get(PATH, params=QUERY)

    assert result.status_code == 403
    assert result.json() == {"success": False, "error": "Forbidden"}
    assert harness.store.seen == []


async def test_an_admin_holding_only_the_other_analytics_permissions_is_403(harness):
    harness.grants["analyst"] = ANALYTICS_ONLY

    async with harness.client("analyst") as client:
        result = await client.get(PATH, params=QUERY)

    assert result.status_code == 403
    assert harness.store.seen == []


@pytest.mark.parametrize(
    "params",
    [
        {"from": "2026-03-10", "to": "2026-03-11"},
        {"merchant_id": "m1", "to": "2026-03-11"},
        {"merchant_id": "m1", "from": "2026-03-10"},
        {**QUERY, "from": "10/03/2026"},
        {**QUERY, "to": "2026-02-30"},
        {**QUERY, "from": "1773100800"},
        {**QUERY, "to": "2026-03-11T00:00:00"},
        {**QUERY, "to": "2026-03-11T00:00:00Z"},
        {**QUERY, "from": "20260310"},
        {**QUERY, "from": "2026-03-10 "},
        {**QUERY, "merchant_id": ""},
        {**QUERY, "merchant_id": "bad id!"},
        {**QUERY, "merchant_id": "x" * 129},
    ],
)
async def test_missing_or_malformed_parameters_are_422(harness, params):
    async with harness.client() as client:
        result = await client.get(PATH, params=params)

    assert result.status_code == 422
    assert result.json() == {"success": False, "error": "Invalid request"}
    assert harness.store.seen == []


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("2026-03-11", "2026-03-10"),
        ("2025-01-01", "2026-01-02"),
        ("1999-12-31", "2000-01-02"),
    ],
)
async def test_a_reversed_or_too_long_range_is_a_422_with_a_code(harness, start, end):
    async with harness.client() as client:
        result = await client.get(PATH, params={**QUERY, "from": start, "to": end})

    assert result.status_code == 422
    assert result.json()["code"] == "invalid_range"
    assert result.headers["cache-control"] == "no-store"
    assert harness.store.seen == []


async def test_a_range_of_exactly_366_days_is_accepted(harness):
    async with harness.client() as client:
        result = await client.get(
            PATH, params={**QUERY, "from": "2025-01-01", "to": "2026-01-01"}
        )

    assert result.status_code == 200
