"""Check that the CPU cost of some work grows linearly with its input, without a wall-clock limit.

A limit such as ``elapsed < 1.0`` measures the machine as much as the code: in the gate (xdist workers,
coverage tracing, the e2e suite, other work on the host) a 0.04 s call has taken 1.1 s. What the
linear-time tests want to prove is how the cost *grows*, so they compare the cost at ``size`` with the
cost at ``size // SCALE_FACTOR`` and bound the ratio:

* CPU time of the calling thread, not wall time: a thread that is starved of the CPU still spends the
  same CPU, so contention from other processes does not move the number much (a busy sibling
  hyper-thread still can, which is why the cheapest of several calls is kept and the two sizes are
  measured in alternation).
* A ratio, not an absolute bound: a constant slowdown (a busy machine, a slower core) cancels. Linear
  work gives about ``SCALE_FACTOR`` (4) and a Python-level quadratic about its square (16);
  ``MAX_GROWTH`` (8) sits between them, so fixed overhead and noise do not trip it.
* Coverage tracing is not a constant factor: it slows Python lines (about 3.5x) but not C calls such as
  ``str.find`` or ``re``. A quadratic that lives in a C call is therefore pulled from 16 towards 4 under
  ``--cov`` and can read under 8 on some inputs (a ``re.sub`` stripper read 7.8 to 7.9 on two of the
  eight shapes in 3 of 16 traced runs). The other shapes still catch it, and without tracing it reads
  11 to 16 everywhere. Nothing here switches the tracer off.
* The CPU clock must be exact. ``time.process_time`` is not on Windows: it counts 15.6 ms scheduler
  ticks, and under load it was seen to charge a 0.4 s batch as 0.14 s, a 3x error that made a linear
  function read as 9x. ``cpu_clock`` therefore reads the thread's cycle counter there
  (``QueryThreadCycleTime``; the unit is cycles, which a ratio does not care about) and
  ``time.thread_time`` (nanosecond CPU time) everywhere else. If the Windows setup fails it raises
  rather than fall back to the clock that was rejected.
* Wall time appears once, only as a cap on retries (``RETRY_BUDGET_SECONDS``): a real regression has
  to run every retry round at full size, minutes for an ``html.parser``-class one. When the budget is
  spent the ratio measured so far decides, so the cap never turns a failure into a pass or the reverse.
"""

import sys
import time
from collections.abc import Callable

SCALE_FACTOR = 4
MAX_GROWTH = 8.0
MIN_ROUNDS = 3
MAX_ROUNDS = 15
RETRY_BUDGET_SECONDS = 60.0


def _windows_cycle_clock() -> Callable[[], float] | None:
    """CPU cycles used so far by the calling thread; None off Windows. A setup error propagates."""
    if sys.platform != "win32":
        return None
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
    timer: Callable[[], float] = time.monotonic,
) -> float:
    """CPU cost of ``run(size)`` divided by that of ``run(size // SCALE_FACTOR)``.

    Each size keeps its cheapest call; the small size, which is the cheap one to repeat, is sampled
    twice per round so a slow start on either side is filtered alike. After ``MIN_ROUNDS`` rounds the
    measurement stops as soon as the ratio is under ``MAX_GROWTH``; while it is not, more rounds (up to
    ``MAX_ROUNDS`` or ``RETRY_BUDGET_SECONDS`` of wall time, whichever comes first) bring both
    cheapest calls closer to their true cost, so a run of noisy calls cannot fail linear work.
    """
    read = clock or cpu_clock()
    small, large = size // SCALE_FACTOR, size
    run(small)  # warm up: first-call costs (imports, caches) belong to neither size
    run(large)
    began = timer()
    best_small = best_large = float("inf")
    ratio = float("inf")
    for done in range(1, MAX_ROUNDS + 1):
        best_small = min(best_small, _cost(run, small, read), _cost(run, small, read))
        best_large = min(best_large, _cost(run, large, read))
        ratio = best_large / best_small if best_small > 0 else float("inf")
        if done >= MIN_ROUNDS and (
            ratio < MAX_GROWTH or timer() - began > RETRY_BUDGET_SECONDS
        ):
            break
    return ratio


def assert_linear(
    run: Callable[[int], object],
    size: int,
    clock: Callable[[], float] | None = None,
    timer: Callable[[], float] = time.monotonic,
) -> None:
    """Fail when growing the input ``SCALE_FACTOR`` times grows the CPU cost by more than ``MAX_GROWTH``."""
    ratio = growth(run, size, clock, timer)
    assert ratio < MAX_GROWTH, (
        f"{SCALE_FACTOR}x the input cost {ratio:.1f}x the CPU time: "
        f"linear work is about {SCALE_FACTOR}x, quadratic about {SCALE_FACTOR**2}x"
    )
