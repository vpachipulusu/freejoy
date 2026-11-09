"""``SlidingWindowLimiter``: never more than N calls in any window, with a fake clock (nothing sleeps)."""

import asyncio
import random

import pytest

from app.core.call_limiter import SlidingWindowLimiter

WINDOW = 60.0
LIMIT = 20


class FakeTime:
    """A clock that only moves when somebody sleeps or the test advances it."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make(fake: FakeTime, *, limit: int = LIMIT) -> SlidingWindowLimiter:
    return SlidingWindowLimiter(limit, WINDOW, clock=fake.clock, sleep=fake.sleep)


async def test_calls_up_to_the_limit_are_not_delayed() -> None:
    fake = FakeTime()
    limiter = make(fake)

    for _ in range(LIMIT):
        await limiter.acquire()

    assert fake.sleeps == []


async def test_the_call_after_the_limit_waits_until_the_oldest_leaves_the_window() -> (
    None
):
    fake = FakeTime()
    limiter = make(fake)
    started = fake.now
    for _ in range(LIMIT):
        await limiter.acquire()

    await limiter.acquire()

    assert len(fake.sleeps) == 1
    assert fake.now - started > WINDOW


async def test_no_window_of_sixty_seconds_ever_holds_more_than_twenty_calls() -> None:
    fake = FakeTime()
    limiter = make(fake)
    rng = random.Random(7)
    stamps: list[float] = []

    for _ in range(300):
        fake.now += rng.choice([0.0, 0.0, 0.1, 1.0, 5.0, 30.0])
        await limiter.acquire()
        stamps.append(fake.now)

    for stamp in stamps:
        in_window = [s for s in stamps if stamp - WINDOW <= s <= stamp]
        assert len(in_window) <= LIMIT


async def test_concurrent_callers_share_the_same_limit() -> None:
    fake = FakeTime()
    limiter = make(fake)
    stamps: list[float] = []

    async def call() -> None:
        await limiter.acquire()
        stamps.append(fake.now)

    await asyncio.gather(*(call() for _ in range(70)))

    for stamp in stamps:
        assert len([s for s in stamps if stamp - WINDOW <= s <= stamp]) <= LIMIT


async def test_a_quiet_minute_resets_the_allowance() -> None:
    fake = FakeTime()
    limiter = make(fake)
    for _ in range(LIMIT):
        await limiter.acquire()
    fake.now += WINDOW + 1

    for _ in range(LIMIT):
        await limiter.acquire()

    assert fake.sleeps == []


@pytest.mark.parametrize(("limit", "window"), [(0, 60.0), (-1, 60.0), (5, 0.0)])
def test_a_limit_or_window_that_makes_no_sense_is_refused(
    limit: int, window: float
) -> None:
    with pytest.raises(ValueError, match="positive"):
        SlidingWindowLimiter(limit, window)
