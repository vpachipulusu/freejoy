/** `helpers/linear-growth`: the check must pass linear work and fail quadratic work, whatever the machine. */
import { describe, expect, it } from 'vitest';
import {
  CATASTROPHIC_GROWTH,
  MAX_GROWTH,
  MAX_ROUNDS,
  MIN_ROUNDS,
  RETRY_BUDGET_MS,
  SCALE_FACTOR,
  expectLinear,
  growth,
  measure,
} from '@/__tests__/unit/helpers/linear-growth';

const UNIT = 1e-3; // fake milliseconds per unit of work

/** A clock that moves only when the work under test says how much time it spent. */
class FakeClock {
  now = 0;
  read = () => this.now;
  linear = (size: number) => {
    this.now += UNIT * size + 0.05; // every real call has some fixed cost
  };
  quadratic = (size: number) => {
    this.now += (UNIT * size * size) / 100;
  };
}

describe('growth and expectLinear', () => {
  it('passes work that grows in step with its input', () => {
    const clock = new FakeClock();

    expectLinear(clock.linear, 4000, clock.read);

    const ratio = growth(clock.linear, 4000, clock.read);
    expect(ratio).toBeGreaterThan(SCALE_FACTOR * 0.5);
    expect(ratio).toBeLessThan(MAX_GROWTH);
  });

  it('fails work that grows with the square of its input, naming both ratios', () => {
    const clock = new FakeClock();

    expect(() => expectLinear(clock.quadratic, 4000, clock.read)).toThrow(
      /4x the input cost 16\.0x the time \(cheapest calls 16\.0x, median calls 16\.0x/
    );
  });

  it('passes a fixed cost that does not grow at all', () => {
    const clock = new FakeClock();

    expectLinear(() => (clock.now += 1), 4000, clock.read);
  });

  it('is not failed by a few slow calls of linear work', () => {
    const clock = new FakeClock();
    let largeCalls = 0;

    expectLinear(
      (size) => {
        clock.linear(size);
        if (size === 4000 && ++largeCalls <= 4) clock.now += 3 * UNIT * size; // warm-up and the first rounds
      },
      4000,
      clock.read
    );
  });

  it('measures the small size first in every round', () => {
    const clock = new FakeClock();
    const sizes: number[] = [];

    measure(
      (size) => {
        sizes.push(size);
        clock.linear(size);
      },
      4000,
      clock.read
    );

    const rounds = sizes.slice(2); // after the two warm-up calls
    expect(rounds.slice(0, 3)).toEqual([1000, 1000, 4000]);
  });

  it('passes on the medians when one lucky fast small call makes the cheapest calls look bad', () => {
    const clock = new FakeClock();
    let smallCalls = 0;

    const ratio = measure(
      (size) => {
        // Large calls always cost 4.0, small calls 1.0, except one lucky 0.1 sample: the minima give 40x.
        if (size === 1000) clock.now += ++smallCalls === 3 ? 0.1 : 1;
        else clock.now += 4;
      },
      4000,
      clock.read,
      () => 0
    );

    expect(ratio.minRatio).toBeGreaterThan(MAX_GROWTH);
    expect(ratio.medianRatio).toBeLessThan(MAX_GROWTH);
    expect(() =>
      expectLinear(
        (size) => {
          clock.now += size === 1000 ? 1 : 4;
        },
        4000,
        clock.read
      )
    ).not.toThrow();
  });

  it('passes on the minima when uniform contention is lifted off the cheapest calls only', () => {
    const clock = new FakeClock();
    let large = 0;

    // Linear work, but every large call except the third is hit by a 5x slowdown: medians read 20x, minima 4x.
    expectLinear(
      (size) => {
        if (size === 1000) clock.now += 1;
        else clock.now += ++large === 3 ? 4 : 20;
      },
      4000,
      clock.read
    );
  });

  it('still fails quadratic work whose small calls were slow at first', () => {
    const clock = new FakeClock();
    let smallCalls = 0;

    expect(() =>
      expectLinear(
        (size) => {
          const before = clock.now;
          clock.quadratic(size);
          if (size === 1000 && ++smallCalls <= 4) clock.now += 1.1 * (clock.now - before);
        },
        4000,
        clock.read
      )
    ).toThrow(/the input cost/);
  });

  it('takes every round for quadratic work while the retry budget lasts', () => {
    const clock = new FakeClock();
    let calls = 0;

    expect(() =>
      expectLinear(
        (size) => {
          calls++;
          clock.quadratic(size);
        },
        4000,
        clock.read,
        () => 0 // the retry budget never runs out
      )
    ).toThrow();

    expect(calls).toBe(2 + 3 * MAX_ROUNDS);
  });

  it('stops taking rounds when the retry budget is spent, and the ratios so far still fail', () => {
    const clock = new FakeClock();
    let calls = 0;
    let readings = 0;

    expect(() =>
      expectLinear(
        (size) => {
          calls++;
          clock.quadratic(size);
        },
        4000,
        clock.read,
        () => (readings++ === 0 ? 0 : 2 * RETRY_BUDGET_MS) // the start, then far past the budget
      )
    ).toThrow();

    expect(calls).toBe(2 + 3 * MIN_ROUNDS);
  });

  it('keeps its retry budget under vitest’s 5 s test timeout', () => {
    expect(RETRY_BUDGET_MS).toBeLessThan(5_000);
  });

  it('refuses a small size that costs nothing the clock can see', () => {
    const clock = new FakeClock();

    expect(() => expectLinear(() => undefined, 4000, clock.read)).toThrow(
      /size too small to measure: 1000/
    );
  });

  it('stops at once on a blow-up instead of measuring it to the end', () => {
    const clock = new FakeClock();
    let calls = 0;

    expect(() =>
      expectLinear(
        (size) => {
          calls++;
          clock.now += size === 1000 ? 1 : 3 * CATASTROPHIC_GROWTH;
        },
        4000,
        clock.read,
        () => 0
      )
    ).toThrow(/the input cost/);

    expect(calls).toBeLessThanOrEqual(2 + 3 * 2); // two rounds, not MAX_ROUNDS
  });
});

describe('on the real clock', () => {
  const linearWork = (size: number) => {
    let total = 0;
    for (let i = 0; i < size; i++) total += i % 7;
    return total;
  };
  const quadraticWork = (size: number) => {
    let total = 0;
    for (let i = 0; i < size; i++) for (let j = 0; j < i; j++) total += j % 7;
    return total;
  };

  it('passes real linear work', () => {
    expectLinear(linearWork, 2_000_000);
  });

  it('fails real quadratic work', () => {
    expect(() => expectLinear(quadraticWork, 3_000)).toThrow(/the input cost/);
  });
});
