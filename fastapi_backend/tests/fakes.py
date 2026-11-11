"""Test doubles for the database session seam (no real database needed)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.exc import OperationalError

from app.core.database import SessionProvider
from app.core.rate_limit import (
    RateLimitDecision,
    RateLimiter,
    RateLimiterUnavailable,
    RateLimitPolicy,
)


class FakeResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one(self) -> Any:
        return self._value


class FakeSession:
    """Stands in for AsyncSession: records statements, returns a canned value."""

    def __init__(self, value: Any = 1, error: Exception | None = None) -> None:
        self.statements: list[str] = []
        self._value = value
        self._error = error

    async def execute(self, statement: Any) -> FakeResult:
        self.statements.append(str(statement))
        if self._error is not None:
            raise self._error
        return FakeResult(self._value)


class FakeSessionProvider:
    """Implements the SessionProvider protocol around a FakeSession."""

    def __init__(self, session: FakeSession) -> None:
        self.fake_session = session
        self.opened = 0
        self.closed = 0

    @asynccontextmanager
    async def session(self) -> AsyncIterator[Any]:
        self.opened += 1
        try:
            yield self.fake_session
        finally:
            self.closed += 1


def failing_session() -> FakeSession:
    return FakeSession(error=OperationalError("SELECT 1", {}, Exception("down")))


def as_provider(provider: FakeSessionProvider) -> SessionProvider:
    """Static proof that the fake satisfies the protocol (checked by mypy)."""
    return provider


class FakeRateLimiter:
    """In-memory sliding-window stand-in: counts hits per identifier."""

    enabled = True

    def __init__(self, reset_at: float = 4_000_000_000.0) -> None:
        self.hits: list[tuple[str, RateLimitPolicy]] = []
        self._counts: dict[str, int] = {}
        self._reset_at = reset_at

    async def hit(self, identifier: str, policy: RateLimitPolicy) -> RateLimitDecision:
        self.hits.append((identifier, policy))
        count = self._counts.get(identifier, 0) + 1
        self._counts[identifier] = count
        return RateLimitDecision(
            allowed=count <= policy.limit,
            limit=policy.limit,
            remaining=max(policy.limit - count, 0),
            reset_at=self._reset_at,
        )


class RaisingRateLimiter:
    """Simulates a Redis outage."""

    enabled = True

    async def hit(self, identifier: str, policy: RateLimitPolicy) -> RateLimitDecision:
        raise RateLimiterUnavailable("redis down")


class ScriptedRateLimiter(FakeRateLimiter):
    """Counts like ``FakeRateLimiter``, with some policy scopes scripted: never limited, always denied, or down.

    Every hit is recorded in ``hits``, scripted or not, so a test can assert the exact sequence of hits.
    """

    def __init__(
        self,
        *,
        unlimited_scopes: set[str] | None = None,
        denied_scopes: set[str] | None = None,
        failing_scopes: set[str] | None = None,
        reset_at: float = 4_000_000_000.0,
    ) -> None:
        super().__init__(reset_at)
        self._unlimited = unlimited_scopes or set()
        self._denied = denied_scopes or set()
        self._failing = failing_scopes or set()

    async def hit(self, identifier: str, policy: RateLimitPolicy) -> RateLimitDecision:
        scope = policy.scope
        if scope in self._failing:
            self.hits.append((identifier, policy))
            raise RateLimiterUnavailable("redis down")
        if scope in self._unlimited or scope in self._denied:
            self.hits.append((identifier, policy))
            return RateLimitDecision(
                scope in self._unlimited, policy.limit, 0, self._reset_at
            )
        return await super().hit(identifier, policy)


class ScopedOutageRateLimiter(ScriptedRateLimiter):
    """Counts like ``FakeRateLimiter``, but a Redis outage hits only the named policy scopes."""

    def __init__(
        self, failing_scopes: set[str], reset_at: float = 4_000_000_000.0
    ) -> None:
        super().__init__(failing_scopes=failing_scopes, reset_at=reset_at)


def as_limiter(limiter: FakeRateLimiter | RaisingRateLimiter) -> RateLimiter:
    """Static proof that the fakes satisfy the protocol (checked by mypy)."""
    return limiter
