"""A merchant's daily budget of partner writes, keyed by the merchant and not by the key (FREEJ-58)."""

import json
from dataclasses import replace
from typing import Any, get_args

import pytest

from app.core import rate_limiter_provider
from app.core.config import settings
from app.core.rate_limit import PARTNER_MERCHANT_WRITE_POLICY
from app.main import app
from app.partner import offer_routes, write_budget
from app.partner.errors import REFUSAL_CODES
from tests.api.partner.support import call_raw
from tests.api.partner.test_partner_errors import FIXED_MESSAGES
from tests.api.partner.test_partner_offer_routes import BODY, FakeService
from tests.fakes import RaisingRateLimiter
from tests.fakes_partner import MERCHANT_ID
from tests.partner_world import PartnerWorld


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch, world: PartnerWorld) -> FakeService:
    fake = FakeService()
    monkeypatch.setattr(settings, "PARTNER_API_ENABLED", True)
    monkeypatch.setattr(
        offer_routes, "build_submission_service", lambda session, context: fake
    )
    monkeypatch.setattr(
        write_budget,
        "PARTNER_MERCHANT_WRITE_POLICY",
        replace(PARTNER_MERCHANT_WRITE_POLICY, limit=2),
    )
    return fake


async def call(
    world: PartnerWorld, method: str, path: str, body: Any = None, key: Any = None
):
    headers = world.headers(key)
    chunks: tuple[bytes, ...] = (b"",)
    if body is not None:
        headers.append(("content-type", "application/json"))
        chunks = (json.dumps(body).encode(),)
    return await call_raw(
        app, method, f"/partner/v1{path}", headers=headers, chunks=chunks
    )


def test_the_policy_is_two_thousand_a_day_and_fails_closed() -> None:
    assert PARTNER_MERCHANT_WRITE_POLICY.scope == "partner_merchant_write"
    assert PARTNER_MERCHANT_WRITE_POLICY.limit == 2000
    assert PARTNER_MERCHANT_WRITE_POLICY.window_seconds == 24 * 60 * 60
    assert PARTNER_MERCHANT_WRITE_POLICY.fail_closed is True


def test_the_new_code_is_one_of_the_closed_set() -> None:
    assert "daily_write_budget" in get_args(REFUSAL_CODES)


async def test_the_write_after_the_budget_is_a_coded_429_and_nothing_is_written(
    world: PartnerWorld, service: FakeService
) -> None:
    first = await call(world, "PUT", "/offers/a", BODY)
    second = await call(world, "POST", "/offers/a/end")
    third = await call(world, "PUT", "/offers/b", BODY)

    assert (first.status, second.status) == (201, 200)
    assert third.status == 429
    assert third.json() == {
        "success": False,
        "error": "Too many requests",
        "code": "daily_write_budget",
    }
    assert third.json()["error"] in FIXED_MESSAGES
    assert third.header("retry-after") is not None
    assert [name for name, _ in service.calls] == ["submit", "end"]


async def test_reads_do_not_spend_the_budget(
    world: PartnerWorld, service: FakeService
) -> None:
    for _ in range(5):
        assert (await call(world, "GET", "/offers")).status == 200
        assert (await call(world, "GET", "/offers/a")).status == 200

    assert (await call(world, "PUT", "/offers/a", BODY)).status == 201


async def test_the_bucket_is_the_merchants_so_a_second_key_of_the_same_merchant_shares_it(
    world: PartnerWorld, service: FakeService
) -> None:
    other = world.store.add(key_id="keyid0000000000000000002", merchant_id=MERCHANT_ID)

    await call(world, "PUT", "/offers/a", BODY)
    await call(world, "PUT", "/offers/b", BODY, other)
    third = await call(world, "PUT", "/offers/c", BODY, other)

    assert third.status == 429
    buckets = {i for i, p in world.limiter.hits if p.scope == "partner_merchant_write"}
    assert buckets == {f"partner_merchant_write:merchant:{MERCHANT_ID}"}


async def test_another_merchant_has_its_own_budget(
    world: PartnerWorld, service: FakeService
) -> None:
    other = world.store.add(
        key_id="keyid0000000000000000003", merchant_id="merchant000000000000000b2"
    )

    for ref in ("a", "b", "c"):
        await call(world, "PUT", f"/offers/{ref}", BODY)
    allowed = await call(world, "PUT", "/offers/a", BODY, other)

    assert allowed.status == 201


async def test_an_outage_of_the_limiter_fails_closed_with_a_503(
    world: PartnerWorld, service: FakeService, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The per-key limit is applied first (and would also fail closed): leave it working and break only this call.
    class Limiter:
        enabled = True

        async def hit(self, identifier: str, policy: Any) -> Any:
            if policy.scope == "partner_merchant_write":
                return await RaisingRateLimiter().hit(identifier, policy)
            return await world.limiter.hit(identifier, policy)

    monkeypatch.setattr(rate_limiter_provider, "_rate_limiter", Limiter())

    response = await call(world, "PUT", "/offers/a", BODY)

    assert response.status == 503
    assert response.json() == {
        "success": False,
        "error": "Service temporarily unavailable",
    }
    assert service.calls == []
