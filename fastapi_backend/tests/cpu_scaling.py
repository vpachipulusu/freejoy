"""Check that the CPU cost of some work grows linearly with its input, without a wall-clock limit.

A limit such as ``elapsed < 1.0`` measures the machine as much as the code: in the gate (xdist workers,
coverage tracing, the e2e suite, other work on the host) a 0.04 s call has taken 1.1 s. What the
linear-time tests want to prove is how the cost *grows*, so they compare the cost at ``size`` with the
cost at ``size // SCALE_FACTOR`` and bound the ratio:

* CPU time of the calling thread, not wall time: a thread that is starved of the CPU still spends the
  same CPU, so contention from other processes does not move the number much (a busy sibling
  hyper-thread still can, which is why the cheapest of several calls is kept and the two sizes are
  measured in alternation).
* A ratio, not an absolute bound: coverage tracing multiplies the cost of both sizes by the same
  constant, and the constant cancels. Linear work gives about ``SCALE_FACTOR`` (4), quadratic work about
  its square (16); ``MAX_GROWTH`` (8) sits between them, so fixed overhead and noise do not trip it and
  an accidental quadratic always does.
* The CPU clock must be exact. ``time.process_time`` is not on Windows: it counts 15.6 ms scheduler
  ticks, and under load it was seen to charge a 0.4 s batch as 0.14 s, a 3x error that made a linear
  function read as 9x. ``cpu_clock`` therefore reads the thread's cycle counter there
  (``QueryThreadCycleTime``; the unit is cycles, which a ratio does not care about) and
  ``time.thread_time`` (nanosecond CPU time) everywhere else.
"""

import sys
import time
from collections.abc import Callable

SCALE_FACTOR = 4
MAX_GROWTH = 8.0
MIN_ROUNDS = 3
MAX_ROUNDS = 15


def _windows_cycle_clock() -> Callable[[], float] | None:
    """CPU cycles used so far by the calling thread, or None when the call is not available."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentThread.restype = wintypes.HANDLE
        kernel32.QueryThreadCycleTime.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ctypes.c_ulonglong),
        ]
        kernel32.QueryThreadCycleTime.restype = wintypes.BOOL
        current = kernel32.GetCurrentThread()
        cycles = ctypes.c_ulonglong()

        def read() -> float:
            if not kernel32.QueryThreadCycleTime(current, ctypes.byref(cycles)):
                raise OSError(ctypes.get_last_error(), "QueryThreadCycleTime failed")
            return float(cycles.value)

        read()
        return read
    except (AttributeError, OSError):
        return None


def cpu_clock() -> Callable[[], float]:
    return _windows_cycle_clock() or time.thread_time


def _cost(run: Callable[[int], object], size: int, clock: Callable[[], float]) -> float:
    started = clock()
    run(size)
    return clock() - started


def growth(
    run: Callable[[int], object],
    size: int,
    clock: Callable[[], float] | None = None,
) -> float:
    """CPU cost of ``run(size)`` divided by that of ``run(size // SCALE_FACTOR)``.

    Each size keeps its cheapest call. After ``MIN_ROUNDS`` rounds the measurement stops as soon as the
    ratio is under ``MAX_GROWTH``; while it is not, more rounds (up to ``MAX_ROUNDS``) bring both
    cheapest calls closer to their true cost, so a run of noisy calls cannot fail linear work but
    real quadratic growth still does.
    """
    read = clock or cpu_clock()
    small, large = size // SCALE_FACTOR, size
    run(small)  # warm up: first-call costs (imports, caches) belong to neither size
    run(large)
    best_small = best_large = float("inf")
    ratio = float("inf")
    for done in range(1, MAX_ROUNDS + 1):
        best_small = min(best_small, _cost(run, small, read))
        best_large = min(best_large, _cost(run, large, read))
        ratio = best_large / best_small if best_small > 0 else float("inf")
        if done >= MIN_ROUNDS and ratio < MAX_GROWTH:
            break
    return ratio


def assert_linear(
    run: Callable[[int], object],
    size: int,
    clock: Callable[[], float] | None = None,
) -> None:
    """Fail when growing the input ``SCALE_FACTOR`` times grows the CPU cost by more than ``MAX_GROWTH``."""
    ratio = growth(run, size, clock)
    assert ratio < MAX_GROWTH, (
        f"{SCALE_FACTOR}x the input cost {ratio:.1f}x the CPU time: "
        f"linear work is about {SCALE_FACTOR}x, quadratic about {SCALE_FACTOR**2}x"
    )
