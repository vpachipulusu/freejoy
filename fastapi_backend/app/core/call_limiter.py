"""A client-side limit on how often an outbound API may be called.

``SlidingWindowLimiter`` keeps the time of each recent call and lets at most ``max_calls`` happen in
any window of ``window_seconds``: a caller that would exceed it sleeps until the oldest call has left
the window. It is a guarantee, not a best effort: a call at time ``t`` is only allowed once fewer than
``max_calls`` calls were made in ``[t - window, t]``. The clock and the sleeper are injected, so tests
run on a fake clock and never sleep. One lock serialises callers, so concurrent tasks share the limit.
"""

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable

# Added to every wait so the oldest call is strictly outside the window when the caller wakes.
WAKE_MARGIN_SECONDS = 0.001

Clock = Callable[[], float]
Sleeper = Callable[[float], Awaitable[None]]


class SlidingWindowLimiter:
    def __init__(
        self,
        max_calls: int,
        window_seconds: float,
        *,
        clock: Clock = time.monotonic,
        sleep: Sleeper = asyncio.sleep,
    ) -> None:
        if max_calls < 1 or window_seconds <= 0:
            raise ValueError("max_calls and window_seconds must be positive")
        self._max_calls = max_calls
        self._window = window_seconds
        self._clock = clock
        self._sleep = sleep
        self._calls: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Return when one more call is allowed, and count it."""
        async with self._lock:
            while True:
                now = self._clock()
                while self._calls and now - self._calls[0] > self._window:
                    self._calls.popleft()
                if len(self._calls) < self._max_calls:
                    self._calls.append(now)
                    return
                wait = self._calls[0] + self._window - now
                await self._sleep(max(wait, 0.0) + WAKE_MARGIN_SECONDS)
