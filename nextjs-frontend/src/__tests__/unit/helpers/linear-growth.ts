/**
 * Check that the cost of some work grows linearly with its input, without a wall-clock limit.
 *
 * A bound such as `elapsed < 50` measures the machine as much as the code: with two suites running side by
 * side a 20 ms scrub has taken more than 50 ms (FREEJ-64). What these tests want to prove is how the cost
 * grows, so they compare the cost at `size` with the cost at `size / SCALE_FACTOR` and bound the ratio. The
 * backend does the same with CPU time (`fastapi_backend/tests/cpu_scaling.py`, FREEJ-61).
 *
 * - A ratio, not an absolute bound: a constant slowdown (a busy machine, a slower core) cancels. Linear work
 *   gives about `SCALE_FACTOR` (4), quadratic work about its square (16) and a backtracking blow-up far more;
 *   `MAX_GROWTH` (8) sits between them, so fixed overhead and noise do not trip it.
 * - Every sample is kept and the sizes are measured in alternation, the small one first. The check passes when
 *   EITHER the ratio of the cheapest calls OR the ratio of the median calls is under the bound. Load only ever
 *   adds time to a call, so the cheapest is closest to the true cost; contention that hits every call alike
 *   cancels in the medians, and one lucky fast call cannot fail the check through the minima. Quadratic work
 *   fails both.
 * - JavaScript has no cheap exact CPU clock. `process.threadCpuUsage()` counts in scheduler ticks on Windows
 *   (about 15.6 ms), too coarse for a call of a few milliseconds, so the clock is `performance.now()`. That
 *   makes the check statistical rather than exact.
 * - Wall time appears only as a cap on retries (`RETRY_BUDGET_MS`), kept below vitest's 5,000 ms test timeout so
 *   a loaded machine gets this helper's own ratio instead of "Test timed out". When the budget is spent the
 *   ratios measured so far decide, so the cap never turns a failure into a pass or the reverse.
 * - A blow-up is not measured to the end: when two large calls in a row cost `CATASTROPHIC_GROWTH` times the
 *   cheapest small call the check stops and fails at once.
 * - A small size that costs nothing the clock can see cannot be compared with anything: that throws.
 */

export const SCALE_FACTOR = 4;
export const MAX_GROWTH = 8;
export const CATASTROPHIC_GROWTH = 200;
export const MIN_ROUNDS = 3;
export const MAX_ROUNDS = 15;
export const RETRY_BUDGET_MS = 2_500;

type Clock = () => number;
type Run = (size: number) => unknown;

export type Measurement = {
  /** Ratio of the cheapest large call to the cheapest small call. */
  readonly minRatio: number;
  /** Ratio of the median large call to the median small call. */
  readonly medianRatio: number;
  readonly rounds: number;
};

function cost(run: Run, size: number, clock: Clock): number {
  const started = clock();
  run(size);
  return clock() - started;
}

function median(samples: readonly number[]): number {
  const sorted = [...samples].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 1 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

/** Costs of `run(size)` against `run(size / SCALE_FACTOR)`; see the file comment for the rules. */
export function measure(
  run: Run,
  size: number,
  clock: Clock = () => performance.now(),
  timer: Clock = () => performance.now()
): Measurement {
  const small = Math.floor(size / SCALE_FACTOR);
  run(small); // warm up: first-call costs (JIT, regex compilation, caches) belong to neither size
  run(size);
  const began = timer();
  const smallSamples: number[] = [];
  const largeSamples: number[] = [];
  let minRatio = Infinity;
  let medianRatio = Infinity;
  let rounds = 0;
  let hugeInARow = 0;
  while (rounds < MAX_ROUNDS) {
    rounds += 1;
    smallSamples.push(cost(run, small, clock), cost(run, small, clock));
    largeSamples.push(cost(run, size, clock));
    const smallest = Math.min(...smallSamples);
    if (!(smallest > 0)) {
      throw new Error(
        `size too small to measure: ${small} costs nothing the clock can see; use a larger size or repeat the work inside run()`
      );
    }
    minRatio = Math.min(...largeSamples) / smallest;
    medianRatio = median(largeSamples) / median(smallSamples);
    hugeInARow =
      largeSamples[largeSamples.length - 1] >= CATASTROPHIC_GROWTH * smallest ? hugeInARow + 1 : 0;
    if (hugeInARow >= 2) break;
    const passes = minRatio < MAX_GROWTH || medianRatio < MAX_GROWTH;
    if (rounds >= MIN_ROUNDS && (passes || timer() - began > RETRY_BUDGET_MS)) break;
  }
  return { minRatio, medianRatio, rounds };
}

/** The better of the two ratios: what the check compares with `MAX_GROWTH`. */
export function growth(
  run: Run,
  size: number,
  clock: Clock = () => performance.now(),
  timer: Clock = () => performance.now()
): number {
  const { minRatio, medianRatio } = measure(run, size, clock, timer);
  return Math.min(minRatio, medianRatio);
}

/** Fails when growing the input `SCALE_FACTOR` times grows the cost by more than `MAX_GROWTH`. */
export function expectLinear(
  run: Run,
  size: number,
  clock: Clock = () => performance.now(),
  timer: Clock = () => performance.now()
): void {
  const { minRatio, medianRatio, rounds } = measure(run, size, clock, timer);
  if (!(minRatio < MAX_GROWTH || medianRatio < MAX_GROWTH)) {
    throw new Error(
      `${SCALE_FACTOR}x the input cost ${Math.min(minRatio, medianRatio).toFixed(1)}x the time ` +
        `(cheapest calls ${minRatio.toFixed(1)}x, median calls ${medianRatio.toFixed(1)}x, ${rounds} rounds): ` +
        `linear work is about ${SCALE_FACTOR}x, quadratic about ${SCALE_FACTOR ** 2}x`
    );
  }
}
