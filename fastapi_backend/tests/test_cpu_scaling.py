"""``tests.cpu_scaling``: the linear-time check must pass linear work and fail quadratic work."""

import pytest

from tests.cpu_scaling import (
    MAX_GROWTH,
    SCALE_FACTOR,
    assert_linear,
    cpu_clock,
    growth,
)

UNIT = 1e-6  # fake seconds of CPU per unit of work


class FakeCpuClock:
    """A CPU clock that moves only when the work under test says how much it spent."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def linear(self, size: int) -> None:
        fixed_overhead = 5e-5  # every real call has some
        self.now += UNIT * size + fixed_overhead

    def quadratic(self, size: int) -> None:
        self.now += UNIT * size * size / 100


def test_work_that_grows_in_step_with_its_input_passes() -> None:
    clock = FakeCpuClock()

    assert_linear(clock.linear, 4000, clock)

    assert SCALE_FACTOR * 0.5 < growth(clock.linear, 4000, clock) < MAX_GROWTH


def test_work_that_grows_with_the_square_of_its_input_fails() -> None:
    clock = FakeCpuClock()

    with pytest.raises(AssertionError, match=r"4x the input cost 16\.0x"):
        assert_linear(clock.quadratic, 4000, clock)


def test_a_fixed_cost_that_does_not_grow_at_all_passes() -> None:
    clock = FakeCpuClock()

    def constant(_: int) -> None:
        clock.now += 1e-3

    assert_linear(constant, 4000, clock)


def test_a_few_slow_calls_do_not_fail_work_that_is_linear() -> None:
    clock = FakeCpuClock()
    large_calls = 0

    def slow_at_first(size: int) -> None:
        nonlocal large_calls
        clock.linear(size)
        if size == 4000:
            large_calls += 1
            if large_calls <= 4:  # the warm-up and the first three rounds
                clock.now += 3 * UNIT * size

    assert_linear(slow_at_first, 4000, clock)


def test_the_cpu_clock_moves_when_the_thread_works() -> None:
    read = cpu_clock()
    before = read()

    sum(1 for _ in range(300_000))

    assert read() > before


def _linear_work(size: int) -> int:
    return sum(1 for _ in range(size))


def _quadratic_work(size: int) -> int:
    return sum(1 for i in range(size) for _ in range(i))


def test_real_linear_work_passes_on_the_real_cpu_clock() -> None:
    assert_linear(_linear_work, 200_000)


def test_real_quadratic_work_fails_on_the_real_cpu_clock() -> None:
    with pytest.raises(AssertionError, match="the input cost"):
        assert_linear(_quadratic_work, 400)
