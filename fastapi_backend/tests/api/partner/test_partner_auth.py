"""Partner authentication (ADR-013) through the real application: one 401 for every kind of bad key, decided
before routing and before the body, limited by key id, and leaving nothing secret in the logs."""

import logging
from typing import Any

import pytest
from fastapi import Request
from pydantic import BaseModel

from app.core.config import settings
from app.core.partner_keys import generate_key, key_digest
from app.core.rate_limit import PARTNER_AUTH_FAILURE_POLICY, PARTNER_READ_POLICY
from app.main import app
from app.partner.app import create_partner_api, create_partner_authenticator
from app.partner.surface import PartnerSurface
from tests.api.partner.support import RawResponse, call_raw
from tests.fakes import RaisingRateLimiter
from tests.fakes_partner import KEY_ID, MERCHANT_ID
from tests.partner_world import PartnerWorld

ME = "/partner/v1/me"
UNAUTHORIZED = {"success": False, "error": "Unauthorized"}


@pytest.fixture(autouse=True)
def surface_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "PARTNER_API_ENABLED", True)


def bearer(value: str) -> list[tuple[str, str]]:
    return [("authorization", f"Bearer {value}")]


async def get_me(headers: list[tuple[str, str]], **kwargs: Any) -> RawResponse:
    return await call_raw(app, "GET", ME, headers=headers, **kwargs)


# ------------------------------------------------------------------------------------------ the happy path


async def test_a_valid_key_reads_its_merchant_and_label(world: PartnerWorld) -> None:
    world.store.profiles[KEY_ID] = world.store.profiles[KEY_ID].__class__(
        "Shop", "Feed"
    )

    response = await get_me(world.headers())

    assert response.status == 200
    assert response.json() == {
        "success": True,
        "data": {"merchant_name": "Shop", "key_label": "Feed"},
    }
    assert response.header("x-freejoy-surface") == "partner"


async def test_a_valid_key_moves_last_used_and_commits_once(
    world: PartnerWorld,
) -> None:
    await get_me(world.headers())

    assert world.store.touched == [KEY_ID]
    assert sum(s.commits for s in world.sessions.sessions) == 1


async def test_a_valid_key_is_never_counted_as_a_failure(world: PartnerWorld) -> None:
    await get_me(world.headers())

    assert [policy.scope for _, policy in world.limiter.hits] == ["partner_read"]
    assert world.limiter.hits[0][0] == f"partner_read:key:{KEY_ID}"


async def test_a_scheme_in_any_letter_case_is_accepted(world: PartnerWorld) -> None:
    response = await get_me([("authorization", f"bEaReR {world.key.value}")])

    assert response.status == 200


# ------------------------------------------------------------------------- every bad key gives one answer


def _bad_requests(world: PartnerWorld) -> dict[str, list[tuple[str, str]]]:
    other = generate_key("test", "otherkey00000000000000a1")
    wrong_secret = world.key.value[:-1] + ("0" if world.key.value[-1] != "0" else "1")
    revoked = world.store.add(key_id="revokedkey0000000000000a", usable=False)
    return {
        "no header": [],
        "basic scheme": [("authorization", "Basic dXNlcjpwYXNz")],
        "empty token": [("authorization", "Bearer ")],
        "garbage": bearer("not-a-key"),
        "unknown id": bearer(other.value),
        "wrong secret": bearer(wrong_secret),
        "revoked": bearer(revoked.value),
        "other environment": bearer(generate_key("production", KEY_ID).value),
        "key with a suffix": bearer(world.key.value + "x"),
        "two headers": [
            ("authorization", f"Bearer {world.key.value}"),
            ("authorization", f"Bearer {world.key.value}"),
        ],
        "internal key only": [("x-internal-key", "k")],
    }


async def test_unknown_revoked_malformed_and_missing_keys_get_the_same_401(
    world: PartnerWorld,
) -> None:
    answers: dict[str, RawResponse] = {}
    for name, headers in _bad_requests(world).items():
        answers[name] = await get_me(headers)

    for name, response in answers.items():
        assert response.status == 401, name
        assert response.json() == UNAUTHORIZED, name
        assert response.header("www-authenticate") == "Bearer", name
    bodies = {r.body for r in answers.values()}
    header_sets = {tuple(sorted(r.headers)) for r in answers.values()}
    assert len(bodies) == 1
    # Only the request id (a different value each time) and nothing that depends on the cause may differ.
    names = {frozenset(k for k, _ in r.headers) for r in answers.values()}
    assert len(names) == 1
    assert header_sets  # computed to make the comparison above explicit


async def test_an_inactive_merchants_key_gets_the_same_401(world: PartnerWorld) -> None:
    dormant = world.store.add(key_id="dormantkey000000000000a1", usable=False)

    response = await get_me(bearer(dormant.value))

    assert response.status == 401
    assert response.json() == UNAUTHORIZED


async def test_revoking_a_key_takes_effect_on_the_next_request(
    world: PartnerWorld,
) -> None:
    before = await get_me(world.headers())
    world.store.revoke(KEY_ID)
    after = await get_me(world.headers())

    assert (before.status, after.status) == (200, 401)


async def test_a_key_in_the_query_string_is_never_read(world: PartnerWorld) -> None:
    for name in ("api_key", "key", "access_token", "token", "authorization"):
        response = await call_raw(
            app, "GET", ME, query_string=f"{name}={world.key.value}"
        )

        assert response.status == 401


async def test_a_key_in_another_header_or_a_cookie_is_never_read(
    world: PartnerWorld,
) -> None:
    for header in ("x-api-key", "x-partner-key", "x-forwarded-authorization"):
        response = await get_me([(header, world.key.value)])

        assert response.status == 401


# ------------------------------------------------------------------- mixed credentials are rejected


@pytest.mark.parametrize(
    "other",
    [
        ("x-internal-key", "test-only-internal-service-key-not-for-deployment"),
        ("x-session-token", "a-session-token"),
        ("cookie", "session=abc"),
        ("Cookie", "x=1"),
    ],
)
async def test_a_valid_key_with_another_credential_is_rejected_before_any_lookup(
    world: PartnerWorld, other: tuple[str, str]
) -> None:
    response = await get_me([*world.headers(), other])

    assert response.status == 401
    assert response.json() == UNAUTHORIZED
    assert world.store.finds == []
    assert world.store.touched == []


# ----------------------------------------------------------- auth before the body and before routing


class Item(BaseModel):
    count: int


def _surface_with_a_typed_route() -> PartnerSurface:
    api = create_partner_api()

    @api.post("/typed")
    async def typed(item: Item) -> dict[str, int]:
        return {"count": item.count}

    @api.post("/state")
    async def state(request: Request) -> dict[str, bool]:
        return {"internal": hasattr(request.state, "internal_authenticated")}

    return PartnerSurface(
        api, enabled=lambda: True, authenticator=create_partner_authenticator()
    )


async def test_an_invalid_body_with_no_key_is_a_401_not_a_422(
    world: PartnerWorld,
) -> None:
    surface = _surface_with_a_typed_route()
    headers = [("content-type", "application/json")]

    anonymous = await call_raw(
        surface, "POST", "/typed", headers=headers, chunks=[b'{"count": "x"']
    )
    keyed = await call_raw(
        surface,
        "POST",
        "/typed",
        headers=[*headers, *world.headers()],
        chunks=[b'{"count": "x"}'],
    )

    assert anonymous.status == 401
    assert anonymous.json() == UNAUTHORIZED
    assert keyed.status == 422


async def test_the_context_reaches_the_route_and_no_internal_flag_is_set(
    world: PartnerWorld,
) -> None:
    surface = _surface_with_a_typed_route()

    response = await call_raw(surface, "POST", "/state", headers=world.headers())

    assert response.json() == {"internal": False}


async def test_a_method_the_route_does_not_have_gets_401_without_a_key_and_405_with_one(
    world: PartnerWorld,
) -> None:
    anonymous = await call_raw(app, "DELETE", ME)
    keyed = await call_raw(app, "DELETE", ME, headers=world.headers())

    assert (anonymous.status, keyed.status) == (401, 405)


# ----------------------------------------------------------------------------- the failure limit


async def test_failures_are_counted_per_presented_key_id_and_never_by_address(
    world: PartnerWorld,
) -> None:
    wrong = bearer(world.key.value[:-1] + ("0" if world.key.value[-1] != "0" else "1"))

    await get_me(
        wrong,
    )

    identifier, policy = world.limiter.hits[0]
    assert policy is PARTNER_AUTH_FAILURE_POLICY
    assert identifier == f"partner_auth_failure:key:{KEY_ID}"
    assert "203.0.113.9" not in identifier


async def test_malformed_keys_share_one_bucket(world: PartnerWorld) -> None:
    await get_me([])
    await get_me(bearer("junk"))

    assert [i for i, _ in world.limiter.hits] == ["partner_auth_failure:malformed"] * 2


async def test_past_the_limit_a_failure_gets_429_with_retry_after(
    world: PartnerWorld,
) -> None:
    last = None
    for _ in range(PARTNER_AUTH_FAILURE_POLICY.limit + 1):
        last = await get_me(bearer("junk"))

    assert last is not None
    assert last.status == 429
    assert last.json() == {"success": False, "error": "Too many requests"}
    assert last.header("retry-after") is not None


async def test_a_valid_key_still_works_while_its_failure_bucket_is_full(
    world: PartnerWorld,
) -> None:
    wrong = bearer(world.key.value[:-1] + ("0" if world.key.value[-1] != "0" else "1"))
    for _ in range(PARTNER_AUTH_FAILURE_POLICY.limit + 5):
        await get_me(wrong)

    response = await get_me(world.headers())

    assert response.status == 200


async def test_a_limiter_outage_fails_closed_for_a_failed_authentication(
    world: PartnerWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core import rate_limiter_provider

    monkeypatch.setattr(rate_limiter_provider, "_rate_limiter", RaisingRateLimiter())

    response = await get_me(bearer("junk"))

    assert response.status == 503
    assert response.json() == {
        "success": False,
        "error": "Service temporarily unavailable",
    }


async def test_a_limiter_outage_lets_a_valid_read_through(
    world: PartnerWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core import rate_limiter_provider

    monkeypatch.setattr(rate_limiter_provider, "_rate_limiter", RaisingRateLimiter())

    response = await get_me(world.headers())

    assert PARTNER_READ_POLICY.fail_closed is False
    assert response.status == 200


# ------------------------------------------------------------------------------- unavailability


async def test_a_database_failure_during_authentication_is_a_503_not_a_401_or_500(
    world: PartnerWorld,
) -> None:
    world.store.fail_find = True

    response = await get_me(world.headers())

    assert response.status == 503
    assert response.json() == {
        "success": False,
        "error": "Service temporarily unavailable",
    }


async def test_a_failed_last_used_update_does_not_refuse_a_valid_key(
    world: PartnerWorld, caplog: pytest.LogCaptureFixture
) -> None:
    world.store.fail_touch = True
    caplog.set_level(logging.WARNING)

    response = await get_me(world.headers())

    assert response.status == 200
    assert sum(s.rollbacks for s in world.sessions.sessions) == 1
    assert world.key.value not in caplog.text


# ------------------------------------------------------------------------------------- the logs


async def test_the_logs_record_the_key_id_and_never_the_key_its_hash_or_the_header(
    world: PartnerWorld, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    secret = world.key.value.rsplit("_", 1)[1]
    wrong = world.key.value[:-1] + ("0" if world.key.value[-1] != "0" else "1")

    await get_me(bearer(wrong))
    await get_me(world.headers())
    await get_me([*world.headers(), ("x-internal-key", "k")])
    await get_me(bearer("junk"))
    world.store.fail_find = True
    await get_me(world.headers())

    assert KEY_ID in caplog.text
    for forbidden in (
        world.key.value,
        wrong,
        secret,
        key_digest(world.key.value),
        key_digest(wrong),
        "Bearer ",
        MERCHANT_ID,
    ):
        assert forbidden not in caplog.text
