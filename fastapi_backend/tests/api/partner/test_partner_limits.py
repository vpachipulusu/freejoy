"""Partner rate limits (ADR-007, ADR-013): keyed by key id, read fails open, write fails closed."""

import pytest

from app.core import rate_limiter_provider
from app.core.config import settings
from app.core.rate_limit import (
    PARTNER_AUTH_FAILURE_POLICY,
    PARTNER_READ_POLICY,
    PARTNER_WRITE_POLICY,
)
from app.partner.app import create_partner_api, create_partner_authenticator
from app.partner.limits import policy_for
from app.partner.surface import PartnerSurface
from tests.api.partner.support import call_raw
from tests.fakes import FakeRateLimiter, RaisingRateLimiter
from tests.fakes_partner import KEY_ID
from tests.partner_world import PartnerWorld


@pytest.fixture(autouse=True)
def surface_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "PARTNER_API_ENABLED", True)


def _surface() -> PartnerSurface:
    api = create_partner_api()

    @api.post("/things")
    async def create_thing() -> dict[str, bool]:
        return {"ok": True}

    @api.put("/things/1")
    async def replace_thing() -> dict[str, bool]:
        return {"ok": True}

    return PartnerSurface(
        api, enabled=lambda: True, authenticator=create_partner_authenticator()
    )


def test_the_policies_are_keyed_apart_and_fail_as_documented() -> None:
    assert PARTNER_READ_POLICY.scope == "partner_read"
    assert PARTNER_READ_POLICY.fail_closed is False
    assert PARTNER_WRITE_POLICY.scope == "partner_write"
    assert PARTNER_WRITE_POLICY.fail_closed is True
    assert PARTNER_AUTH_FAILURE_POLICY.scope == "partner_auth_failure"
    assert PARTNER_AUTH_FAILURE_POLICY.fail_closed is True


@pytest.mark.parametrize(
    ("method", "policy"),
    [
        ("GET", PARTNER_READ_POLICY),
        ("get", PARTNER_READ_POLICY),
        ("HEAD", PARTNER_READ_POLICY),
        ("OPTIONS", PARTNER_READ_POLICY),
        ("POST", PARTNER_WRITE_POLICY),
        ("PUT", PARTNER_WRITE_POLICY),
        ("PATCH", PARTNER_WRITE_POLICY),
        ("DELETE", PARTNER_WRITE_POLICY),
        ("PROPFIND", PARTNER_WRITE_POLICY),
    ],
)
def test_a_read_method_gets_the_read_policy_and_anything_else_the_write_policy(
    method: str, policy: object
) -> None:
    assert policy_for(method) is policy


async def test_a_read_is_keyed_by_the_key_id_and_carries_rate_limit_headers(
    world: PartnerWorld,
) -> None:
    response = await call_raw(_surface(), "GET", "/me", headers=world.headers())

    assert response.status == 200
    assert world.limiter.hits == [(f"partner_read:key:{KEY_ID}", PARTNER_READ_POLICY)]
    assert response.header("x-ratelimit-limit") == str(PARTNER_READ_POLICY.limit)


async def test_a_write_is_keyed_by_the_key_id_under_the_write_policy(
    world: PartnerWorld,
) -> None:
    response = await call_raw(_surface(), "POST", "/things", headers=world.headers())

    assert response.status == 200
    assert world.limiter.hits == [(f"partner_write:key:{KEY_ID}", PARTNER_WRITE_POLICY)]


async def test_past_the_write_limit_a_write_gets_429(world: PartnerWorld) -> None:
    surface = _surface()
    last = None
    for _ in range(PARTNER_WRITE_POLICY.limit + 1):
        last = await call_raw(surface, "PUT", "/things/1", headers=world.headers())

    assert last is not None
    assert last.status == 429
    assert last.header("retry-after") is not None


async def test_the_read_and_write_budgets_are_separate(world: PartnerWorld) -> None:
    surface = _surface()
    for _ in range(PARTNER_WRITE_POLICY.limit + 1):
        await call_raw(surface, "POST", "/things", headers=world.headers())

    read = await call_raw(surface, "GET", "/me", headers=world.headers())

    assert read.status == 200


async def test_another_key_does_not_share_a_budget(world: PartnerWorld) -> None:
    surface = _surface()
    second = world.store.add(key_id="secondkey0000000000000a1")
    for _ in range(PARTNER_WRITE_POLICY.limit + 1):
        await call_raw(surface, "POST", "/things", headers=world.headers())

    response = await call_raw(surface, "POST", "/things", headers=world.headers(second))

    assert response.status == 200


async def test_a_limiter_outage_fails_a_write_closed(
    world: PartnerWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rate_limiter_provider, "_rate_limiter", RaisingRateLimiter())

    response = await call_raw(_surface(), "POST", "/things", headers=world.headers())

    assert response.status == 503
    assert response.header("retry-after") is not None


async def test_the_limiter_the_partner_surface_uses_is_the_shared_one(
    world: PartnerWorld,
) -> None:
    assert isinstance(world.limiter, FakeRateLimiter)
    assert rate_limiter_provider.get_rate_limiter() is world.limiter
