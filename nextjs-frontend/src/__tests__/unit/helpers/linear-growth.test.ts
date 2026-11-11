/** `helpers/linear-growth`: the check must pass linear work and fail quadratic work, whatever the machine. */
import { describe, expect, it } from 'vitest';
import {
  MAX_GROWTH,
  MAX_ROUNDS,
  MIN_ROUNDS,
  SCALE_FACTOR,
  RETRY_BUDGET_MS,
  expectLinear,
  growth,
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

  it('fails work that grows with the square of its input', () => {
    const clock = new FakeClock();

    expect(() => expectLinear(clock.quadratic, 4000, clock.read)).toThrow(
      /4x the input cost 16\.0x/
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

  it('takes every round for quadratic work that stays within the retry budget', () => {
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

  it('stops taking rounds when the retry budget is spent, and the ratio so far still fails', () => {
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
    expect(() => expectLinear(quadraticWork, 8_000)).toThrow(/the input cost/);
  });
});
