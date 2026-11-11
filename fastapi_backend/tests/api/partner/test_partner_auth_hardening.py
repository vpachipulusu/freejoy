"""Partner authentication: what it must not do while a database session is open, how the environment is treated,
what is logged when it breaks, and the paths no other test reached (ADR-013, FREEJ-57 review round)."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from pydantic import SecretStr

from app.core import rate_limiter_provider
from app.core.config import settings
from app.core.database import PartnerDatabaseUnavailable, PartnerSessionProvider
from app.core.partner_keys import generate_key, key_digest
from app.core.rate_limit import (
    PARTNER_AUTH_FAILURE_POLICY,
    RateLimitDecision,
    RateLimitPolicy,
)
from app.main import app
from app.partner import auth as auth_module
from app.partner import deps
from app.partner.app import create_partner_api
from tests.api.partner.support import RawResponse, call_raw
from tests.fakes import FakeRateLimiter
from tests.fakes_partner import KEY_ID, FakePartnerSession
from tests.partner_world import PartnerWorld

ME = "/partner/v1/me"


@pytest.fixture(autouse=True)
def surface_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "PARTNER_API_ENABLED", True)


def bearer(value: str) -> list[tuple[str, str]]:
    return [("authorization", f"Bearer {value}")]


async def get_me(headers: list[tuple[str, str]]) -> RawResponse:
    return await call_raw(app, "GET", ME, headers=headers)


class CountingSessions:
    """A session provider that knows how many sessions are open right now."""

    def __init__(self) -> None:
        self.open = 0

    @asynccontextmanager
    async def session(self) -> AsyncIterator[Any]:
        self.open += 1
        try:
            yield FakePartnerSession()
        finally:
            self.open -= 1


class RecordingLimiter(FakeRateLimiter):
    """Records how many database sessions were open when the limiter was called."""

    def __init__(self, sessions: CountingSessions) -> None:
        super().__init__()
        self.sessions = sessions
        self.open_at_hit: list[int] = []

    async def hit(self, identifier: str, policy: RateLimitPolicy) -> RateLimitDecision:
        self.open_at_hit.append(self.sessions.open)
        return await super().hit(identifier, policy)


def kid(name: str) -> str:
    return name.ljust(24, "0")[:24]


def _wrong_secret(key: str) -> str:
    return key[:-1] + ("0" if key[-1] != "0" else "1")


# ------------------------------------------------------------- no limiter call while a session is open


async def test_no_limiter_call_runs_while_a_database_session_is_open(
    world: PartnerWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    sessions = CountingSessions()
    limiter = RecordingLimiter(sessions)
    monkeypatch.setattr(deps, "get_partner_session_provider", lambda: sessions)
    monkeypatch.setattr(rate_limiter_provider, "_rate_limiter", limiter)
    other = generate_key("test", kid("otherkey"))
    revoked = world.store.add(key_id=kid("revokedkey"), usable=False)
    dormant = world.store.add(key_id=kid("dormantkey"), usable=False)

    for value in (
        other.value,  # unknown
        _wrong_secret(world.key.value),  # wrong secret
        revoked.value,
        dormant.value,
    ):
        assert (await get_me(bearer(value))).status == 401
    assert (await get_me(world.headers())).status == 200  # and the per-key limit

    assert limiter.open_at_hit == [0, 0, 0, 0, 0]


# ------------------------------------------------------------------------------- the environment


async def test_a_wrong_environment_key_and_an_unknown_key_get_identical_status_sequences(
    world: PartnerWorld,
) -> None:
    other_env = generate_key("production", kid("otherkey"))
    unknown = generate_key("test", kid("unknownkey"))
    limit = PARTNER_AUTH_FAILURE_POLICY.limit

    first = [(await get_me(bearer(other_env.value))).status for _ in range(limit + 1)]
    second = [(await get_me(bearer(unknown.value))).status for _ in range(limit + 1)]

    assert first == second == [401] * limit + [429]


async def test_a_wrong_environment_key_does_the_same_lookup_and_comparison_as_any_other(
    world: PartnerWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str | None] = []
    real = auth_module.key_matches

    def recording(presented: str, stored: str | None) -> bool:
        calls.append(stored)
        return real(presented, stored)

    monkeypatch.setattr(auth_module, "key_matches", recording)
    elsewhere = world.store.add(key_id=kid("preview"), environment="production")

    await get_me(bearer(elsewhere.value))  # issued for another environment, id known
    await get_me(bearer(generate_key("production", kid("nosuchkey")).value))

    assert world.store.finds == [kid("preview"), kid("nosuchkey")]
    assert calls[0] == key_digest(
        elsewhere.value
    )  # compared, then refused for its environment
    assert calls[1] is None  # the dummy comparison for an unknown id


async def test_a_key_for_another_environment_is_refused_even_when_it_is_genuine(
    world: PartnerWorld,
) -> None:
    elsewhere = world.store.add(key_id=kid("preview"), environment="production")

    response = await get_me(bearer(elsewhere.value))

    assert response.status == 401
    assert world.store.touched == []


async def test_with_the_store_down_a_wrong_environment_key_and_a_right_one_get_the_same_status(
    world: PartnerWorld,
) -> None:
    world.store.fail_find = True
    other_env = generate_key("production", kid("otherkey"))
    same_env = generate_key("test", kid("otherkey"))

    statuses = {
        (await get_me(bearer(other_env.value))).status,
        (await get_me(bearer(same_env.value))).status,
    }

    assert statuses == {503}


# ---------------------------------------------------------------------------- a broken authentication


class _Unexpected(Exception):
    pass


async def test_an_unexpected_error_is_a_503_with_a_traceback_in_the_log_and_nothing_secret(
    world: PartnerWorld,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    reported: list[BaseException] = []
    monkeypatch.setattr(auth_module, "report_exception", reported.append)

    async def broken(key_id: str) -> None:
        raise _Unexpected("internal detail")

    monkeypatch.setattr(world.store, "find", broken)
    caplog.set_level(logging.ERROR)

    response = await get_me(world.headers())

    assert response.status == 503
    (record,) = [r for r in caplog.records if r.name == "yourapp.partner"]
    assert record.exc_info is not None
    assert isinstance(record.exc_info[1], _Unexpected)
    assert len(reported) == 1
    text = caplog.text
    for forbidden in (
        world.key.value,
        world.key.value.rsplit("_", 1)[1],
        key_digest(world.key.value),
        "Bearer",
        "authorization",
    ):
        assert forbidden not in text


# ------------------------------------------------------------------------------ the comparison


async def test_an_unknown_id_is_compared_once_before_the_branch(
    world: PartnerWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hmac

    calls: list[int] = []
    real = hmac.compare_digest

    def counting(a: Any, b: Any) -> bool:
        calls.append(1)
        return real(a, b)

    monkeypatch.setattr("app.core.partner_keys.hmac.compare_digest", counting)

    await get_me(bearer(generate_key("test", kid("unknownkey")).value))

    assert len(calls) == 1


# ------------------------------------------------------------------------------ the body, the touch


async def test_a_refused_request_never_reads_its_body(world: PartnerWorld) -> None:
    reads: list[int] = []

    response = await call_raw(
        app,
        "POST",
        "/partner/v1/me",
        headers=[("content-length", "5")],
        chunks=[b"hello"],
        on_receive=lambda: reads.append(1),
    )

    assert response.status == 401
    assert reads == []


async def test_a_key_used_again_inside_the_minute_is_not_written_again(
    world: PartnerWorld,
) -> None:
    await get_me(world.headers())
    await get_me(world.headers())
    await get_me(world.headers())

    assert world.store.touched == [KEY_ID]
    assert sum(s.commits for s in world.sessions.sessions) == 1


# -------------------------------------------------------------------- the paths no other test reached


async def test_the_sub_app_without_its_surface_refuses_even_a_valid_key(
    world: PartnerWorld,
) -> None:
    response = await call_raw(
        create_partner_api(), "GET", "/me", headers=world.headers()
    )

    assert response.status == 401
    assert response.header("www-authenticate") == "Bearer"
    assert response.json() == {"success": False, "error": "Unauthorized"}


def test_the_real_lazy_provider_is_built_once_from_the_configured_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "postgresql+asyncpg://freejoy_partner:pw@localhost:1/app"
    monkeypatch.setattr(deps, "_provider", None)
    monkeypatch.setattr(settings, "PARTNER_DATABASE_URL", SecretStr(url))

    first = deps.get_partner_session_provider()
    second = deps.get_partner_session_provider()

    assert first is second
    assert isinstance(first, PartnerSessionProvider)
    assert first._database_url == url


async def test_without_a_partner_database_url_a_valid_looking_key_is_a_503_not_a_500(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(deps, "_provider", None)
    monkeypatch.setattr(settings, "PARTNER_DATABASE_URL", None)
    monkeypatch.setattr(rate_limiter_provider, "_rate_limiter", FakeRateLimiter())
    caplog.set_level(logging.ERROR)

    response = await get_me(
        bearer(generate_key("test", "keyid0000000000000000001").value)
    )

    assert response.status == 503
    record = next(r for r in caplog.records if r.name == "yourapp.partner")
    assert record.exc_info is not None
    assert isinstance(record.exc_info[1], PartnerDatabaseUnavailable)
