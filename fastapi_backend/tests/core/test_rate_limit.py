import asyncio
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from upstash_ratelimit.utils import ms_to_s

from app.api import deps
from app.core import rate_limit as rate_limit_module
from app.core import rate_limiter_provider
from app.core.rate_limit import (
    API_POLICY,
    AUTH_POLICY,
    FAIL_CLOSED_REDIS_TIMEOUT_SECONDS,
    REDIS_TIMEOUT_SECONDS,
    DisabledRateLimiter,
    RateLimiterUnavailable,
    UpstashRateLimiter,
)


class FakeRedis:
    """The adapter only hands the client to upstash-ratelimit; no calls are made."""


class FakeSlidingWindow:
    def __init__(
        self,
        response: Any = None,
        delay: float = 0,
        on_call: Callable[[], None] | None = None,
    ) -> None:
        self.response = response
        self.delay = delay
        self.on_call = on_call
        self.identifiers: list[str] = []
        self.redis: Any = (
            None  # the client this window was built on (set by FakeRatelimit)
        )

    async def limit(self, identifier: str) -> Any:
        self.identifiers.append(identifier)
        if self.on_call is not None:
            self.on_call()
        await asyncio.sleep(self.delay)
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


def limiter_with(
    ratelimit: FakeSlidingWindow, redis: FakeRedis | None = None, timeout: float = 0.05
) -> UpstashRateLimiter:
    limiter = UpstashRateLimiter(redis or FakeRedis(), timeout_seconds=timeout)  # type: ignore[arg-type]
    limiter._limiters[API_POLICY] = ratelimit  # type: ignore[assignment]
    return limiter


def test_redis_timeout_is_200_ms() -> None:
    assert REDIS_TIMEOUT_SECONDS == 0.2


async def test_hit_maps_the_upstash_response() -> None:
    fake = FakeSlidingWindow(
        SimpleNamespace(allowed=True, limit=100, remaining=7, reset=12.5)
    )

    decision = await limiter_with(fake).hit("api:user:u1", API_POLICY)

    assert (decision.allowed, decision.limit, decision.remaining) == (True, 100, 7)
    assert decision.reset_at == 12.5
    assert fake.identifiers == ["api:user:u1"]


async def test_hit_clamps_negative_remaining_to_zero() -> None:
    fake = FakeSlidingWindow(
        SimpleNamespace(allowed=False, limit=100, remaining=-3, reset=1)
    )

    decision = await limiter_with(fake).hit("x", API_POLICY)

    assert (decision.allowed, decision.remaining) == (False, 0)


async def test_hit_raises_unavailable_on_timeout() -> None:
    fake = FakeSlidingWindow(SimpleNamespace(), delay=1)

    with pytest.raises(RateLimiterUnavailable):
        await limiter_with(fake, timeout=0.01).hit("x", API_POLICY)


async def test_hit_raises_unavailable_on_redis_error() -> None:
    fake = FakeSlidingWindow(ConnectionError("down"))

    with pytest.raises(RateLimiterUnavailable):
        await limiter_with(fake).hit("x", API_POLICY)


def test_limiter_is_built_once_per_policy_with_the_rl_prefix() -> None:
    limiter = UpstashRateLimiter.from_credentials("https://x.upstash.io", "token")

    first = limiter._limiter_for(API_POLICY)

    assert limiter._limiter_for(API_POLICY) is first
    assert first._prefix == "rl"


async def test_disabled_limiter_allows_everything() -> None:
    limiter = DisabledRateLimiter()

    assert (await limiter.hit("x", API_POLICY)).allowed is True


def test_get_rate_limiter_is_disabled_without_upstash_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rate_limiter_provider, "_rate_limiter", None)
    monkeypatch.setattr(rate_limiter_provider.settings, "UPSTASH_REDIS_REST_URL", None)

    assert isinstance(deps.get_rate_limiter(), DisabledRateLimiter)


def test_get_rate_limiter_builds_upstash_and_caches_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rate_limiter_provider, "_rate_limiter", None)
    monkeypatch.setattr(
        rate_limiter_provider.settings, "UPSTASH_REDIS_REST_URL", "https://x.upstash.io"
    )
    monkeypatch.setattr(
        rate_limiter_provider.settings, "UPSTASH_REDIS_REST_TOKEN", SecretStr("t")
    )

    limiter = deps.get_rate_limiter()

    assert isinstance(limiter, UpstashRateLimiter)
    assert deps.get_rate_limiter() is limiter


def test_upstash_reset_is_unix_seconds_not_milliseconds() -> None:
    # upstash-ratelimit 1.2.0 documents Response.reset as "Unix timestamp in
    # seconds" and builds it with ms_to_s. If that ever changes, the
    # X-RateLimit-Reset / Retry-After conversion would be wrong: fail loudly.
    assert ms_to_s(1_700_000_000_500) == 1_700_000_000.5


async def test_decision_reset_at_keeps_upstash_seconds_unchanged() -> None:
    fake = FakeSlidingWindow(
        SimpleNamespace(allowed=True, limit=5, remaining=4, reset=1_700_000_000.5)
    )

    decision = await limiter_with(fake).hit("x", API_POLICY)

    assert decision.reset_at == 1_700_000_000.5


# --- a stale or broken Redis client must not lock everyone out ---------------


class Clock:
    """An injected clock: tests advance time instead of sleeping."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class Script:
    """Replaces ``Ratelimit`` so the real ``_limiter_for`` and cache run against scripted windows."""

    def __init__(self, *windows: FakeSlidingWindow) -> None:
        self.windows = list(windows)
        self.clients: list[Any] = []

    def make_client(self) -> Any:
        client = FakeRedis()
        self.clients.append(client)
        return client

    def __call__(self, redis: Any, _algorithm: Any, prefix: str | None = None) -> Any:
        window = self.windows.pop(0)
        window.redis = redis
        return window


OK = SimpleNamespace(allowed=True, limit=5, remaining=4, reset=1)


@pytest.fixture
def script(monkeypatch: pytest.MonkeyPatch) -> Callable[..., Script]:
    def install(*windows: FakeSlidingWindow) -> Script:
        scripted = Script(*windows)
        monkeypatch.setattr(rate_limit_module, "Ratelimit", scripted)
        return scripted

    return install


def make_limiter(scripted: Script, **kwargs: Any) -> UpstashRateLimiter:
    kwargs.setdefault("rebuild_cooldown_seconds", 0.0)
    kwargs.setdefault("close_grace_seconds", 0)
    return UpstashRateLimiter(
        scripted.make_client(),
        redis_factory=scripted.make_client,
        **kwargs,
    )


async def test_auth_hit_replaces_the_client_and_retries_after_a_connect_error(
    script: Callable[..., Script],
) -> None:
    first = FakeSlidingWindow(httpx.ConnectError("[Errno 16] Device or resource busy"))
    second = FakeSlidingWindow(OK)
    scripted = script(first, second)
    limiter = make_limiter(scripted)

    decision = await limiter.hit("auth:ip:1", AUTH_POLICY)

    assert decision.allowed is True
    assert first.identifiers == ["auth:ip:1"] and second.identifiers == ["auth:ip:1"]
    assert len(scripted.clients) == 2
    assert first.redis is scripted.clients[0] and second.redis is scripted.clients[1]


async def test_auth_hit_is_unavailable_when_the_retry_also_fails(
    script: Callable[..., Script],
) -> None:
    first = FakeSlidingWindow(httpx.ConnectError("down"))
    second = FakeSlidingWindow(httpx.ConnectError("still down"))
    limiter = make_limiter(script(first, second))

    with pytest.raises(RateLimiterUnavailable):
        await limiter.hit("auth:ip:1", AUTH_POLICY)

    assert (len(first.identifiers), len(second.identifiers)) == (1, 1)  # one retry only


async def test_rebuilds_respect_a_cooldown(script: Callable[..., Script]) -> None:
    clock = Clock()
    broken = [FakeSlidingWindow(httpx.ConnectError("down")) for _ in range(2)]
    scripted = script(*broken, FakeSlidingWindow(OK))
    limiter = make_limiter(scripted, rebuild_cooldown_seconds=1.0, clock=clock)

    with pytest.raises(RateLimiterUnavailable):
        await limiter.hit("auth:ip:1", AUTH_POLICY)
    # Failure 1 replaced the client; failure 2 (still inside the cooldown) did not.
    assert len(scripted.clients) == 2

    clock.advance(2.0)
    assert (await limiter.hit("auth:ip:1", AUTH_POLICY)).allowed is True
    assert len(scripted.clients) == 3  # replaced again once the cooldown passed


async def test_a_fail_open_policy_is_never_retried(
    script: Callable[..., Script],
) -> None:
    only = FakeSlidingWindow(httpx.ConnectError("down"))
    scripted = script(only)
    limiter = make_limiter(scripted)

    with pytest.raises(RateLimiterUnavailable):
        await limiter.hit("api:user:u1", API_POLICY)

    assert only.identifiers == ["api:user:u1"]
    assert len(scripted.clients) == 2  # a transport failure still replaces the client


async def test_a_timeout_is_neither_retried_nor_discards_the_warm_client(
    script: Callable[..., Script],
) -> None:
    slow = FakeSlidingWindow(TimeoutError())
    scripted = script(slow)
    limiter = make_limiter(scripted)

    with pytest.raises(RateLimiterUnavailable):
        await limiter.hit("auth:ip:1", AUTH_POLICY)

    assert slow.identifiers == ["auth:ip:1"] and len(scripted.clients) == 1


async def test_a_transport_error_that_is_not_a_connect_error_is_not_retried(
    script: Callable[..., Script],
) -> None:
    first = FakeSlidingWindow(httpx.ReadError("reset"))
    scripted = script(first, FakeSlidingWindow(OK))
    limiter = make_limiter(scripted)

    with pytest.raises(RateLimiterUnavailable):
        await limiter.hit("auth:ip:1", AUTH_POLICY)

    assert first.identifiers == ["auth:ip:1"] and len(scripted.clients) == 2


async def test_a_connect_timeout_is_retried_like_a_connect_error(
    script: Callable[..., Script],
) -> None:
    limiter = make_limiter(
        script(FakeSlidingWindow(httpx.ConnectTimeout("slow")), FakeSlidingWindow(OK))
    )

    assert (await limiter.hit("auth:ip:1", AUTH_POLICY)).allowed is True


async def test_a_retry_that_would_outlive_the_shared_deadline_is_skipped(
    script: Callable[..., Script],
) -> None:
    clock = Clock()
    first = FakeSlidingWindow(
        httpx.ConnectError("down"), on_call=lambda: clock.advance(0.9)
    )
    limiter = make_limiter(script(first, FakeSlidingWindow(OK)), clock=clock)

    with pytest.raises(RateLimiterUnavailable):
        await limiter.hit("auth:ip:1", AUTH_POLICY)  # 0.1 s of the 1 s budget left

    assert first.identifiers == ["auth:ip:1"]


async def test_concurrent_failures_on_one_client_cause_a_single_replacement(
    script: Callable[..., Script],
) -> None:
    scripted = script(
        FakeSlidingWindow(httpx.ConnectError("down")), FakeSlidingWindow(OK)
    )
    limiter = make_limiter(scripted)

    decisions = await asyncio.gather(
        *(limiter.hit(f"auth:ip:{n}", AUTH_POLICY) for n in range(20))
    )

    assert all(decision.allowed for decision in decisions)
    assert len(scripted.clients) == 2  # the initial client plus exactly one replacement


async def test_a_replaced_client_is_closed_after_the_grace_period(
    script: Callable[..., Script],
) -> None:
    closed: list[bool] = []

    class ClosableRedis(FakeRedis):
        async def close(self) -> None:
            closed.append(True)

    scripted = script(
        FakeSlidingWindow(httpx.ConnectError("down")), FakeSlidingWindow(OK)
    )
    limiter = UpstashRateLimiter(
        ClosableRedis(),  # type: ignore[arg-type]
        redis_factory=scripted.make_client,
        rebuild_cooldown_seconds=0,
        close_grace_seconds=0,
    )

    await limiter.hit("auth:ip:1", AUTH_POLICY)
    await asyncio.gather(*limiter._closing)

    assert closed == [True]


async def test_the_same_event_loop_does_not_replace_the_client(
    script: Callable[..., Script],
) -> None:
    scripted = script(FakeSlidingWindow(OK))
    limiter = make_limiter(scripted)

    await limiter.hit("auth:ip:1", AUTH_POLICY)
    await limiter.hit("auth:ip:2", AUTH_POLICY)

    assert len(scripted.clients) == 1


def test_a_new_event_loop_gets_a_fresh_client(script: Callable[..., Script]) -> None:
    scripted = script(FakeSlidingWindow(OK), FakeSlidingWindow(OK))
    limiter = make_limiter(scripted)

    asyncio.run(limiter.hit("auth:ip:1", AUTH_POLICY))
    asyncio.run(limiter.hit("auth:ip:1", AUTH_POLICY))  # a second, different loop

    assert len(scripted.clients) == 2


async def test_a_limiter_without_a_factory_is_never_retried_or_replaced() -> None:
    fake = FakeSlidingWindow(httpx.ConnectError("down"))
    limiter = UpstashRateLimiter(FakeRedis())  # type: ignore[arg-type]
    limiter._limiters[AUTH_POLICY] = fake  # type: ignore[assignment]

    with pytest.raises(RateLimiterUnavailable):
        await limiter.hit("auth:ip:1", AUTH_POLICY)

    assert fake.identifiers == ["auth:ip:1"]


async def test_each_policy_gets_its_own_latency_budget(
    monkeypatch: pytest.MonkeyPatch, script: Callable[..., Script]
) -> None:
    seen: list[float] = []
    real_wait_for = asyncio.wait_for

    async def spy(awaitable: Any, timeout: float) -> Any:
        seen.append(timeout)
        return await real_wait_for(awaitable, timeout)

    scripted = script(FakeSlidingWindow(OK), FakeSlidingWindow(OK))
    limiter = make_limiter(scripted, clock=Clock())
    monkeypatch.setattr(rate_limit_module.asyncio, "wait_for", spy)

    await limiter.hit("auth:ip:1", AUTH_POLICY)
    await limiter.hit("api:user:u1", API_POLICY)

    assert seen == [FAIL_CLOSED_REDIS_TIMEOUT_SECONDS, REDIS_TIMEOUT_SECONDS]


def test_from_credentials_wires_a_factory_that_builds_fresh_clients() -> None:
    limiter = UpstashRateLimiter.from_credentials("https://x.upstash.io", "token")

    assert limiter._redis_factory is not None
    assert limiter._redis_factory() is not limiter._redis


def test_replacing_the_client_builds_new_limiters_bound_to_the_new_client() -> None:
    limiter = UpstashRateLimiter.from_credentials("https://x.upstash.io", "token")
    first = limiter._limiter_for(API_POLICY)
    assert limiter._limiter_for(API_POLICY) is first

    old = limiter._replace_client()
    second = limiter._limiter_for(API_POLICY)

    assert second is not first
    assert second._redis is limiter._redis and second._redis is not old


async def test_a_failing_factory_still_ends_as_unavailable(
    script: Callable[..., Script],
) -> None:
    scripted = script(FakeSlidingWindow(httpx.ConnectError("down")))
    initial = scripted.make_client()

    def broken_factory() -> Any:
        raise RuntimeError("cannot build a client")

    limiter = UpstashRateLimiter(
        initial, redis_factory=broken_factory, rebuild_cooldown_seconds=0
    )

    with pytest.raises(RateLimiterUnavailable):
        await limiter.hit("auth:ip:1", AUTH_POLICY)

    assert limiter._redis is initial  # the old client is kept


async def test_a_failed_call_logs_policy_and_error_class_only(
    caplog: pytest.LogCaptureFixture, script: Callable[..., Script]
) -> None:
    limiter = make_limiter(
        script(
            FakeSlidingWindow(httpx.ConnectError("secret-token-text")),
            FakeSlidingWindow(OK),
        )
    )

    with caplog.at_level("WARNING", logger="yourapp.rate_limit"):
        await limiter.hit("auth:email:digest", AUTH_POLICY)

    text = " ".join(record.getMessage() for record in caplog.records)
    assert "policy=auth" in text and "error=ConnectError" in text
    assert "secret-token-text" not in text and "digest" not in text
