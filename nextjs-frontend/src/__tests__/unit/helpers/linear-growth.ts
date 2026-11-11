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
 * - The cheapest call of each size is kept, and the sizes are measured in alternation: load only ever adds
 *   time to a call, so the cheapest of several is the closest to the real cost. If the ratio is still too
 *   high after `MIN_ROUNDS`, more rounds (up to `MAX_ROUNDS`) are taken before the test fails.
 * - JavaScript has no cheap exact CPU clock. `process.threadCpuUsage()` counts in scheduler ticks on Windows
 *   (about 15.6 ms), too coarse for a call of a few milliseconds, so the clock here is `performance.now()`
 *   and the cheapest-of-rounds rule does the filtering. That makes the check statistical rather than exact:
 *   it fails only if every large call in every round is slow relative to the cheapest small one.
 * - Wall time appears once, as a cap on retries (`RETRY_BUDGET_MS`): a real regression runs every round at
 *   full size. When the budget is spent the ratio measured so far decides, so the cap never turns a failure
 *   into a pass or the reverse.
 */

export const SCALE_FACTOR = 4;
export const MAX_GROWTH = 8;
export const MIN_ROUNDS = 3;
export const MAX_ROUNDS = 15;
export const RETRY_BUDGET_MS = 60_000;

type Clock = () => number;

function cost(run: (size: number) => unknown, size: number, clock: Clock): number {
  const started = clock();
  run(size);
  return clock() - started;
}

/** Cost of `run(size)` divided by the cost of `run(size / SCALE_FACTOR)`, each at its cheapest call. */
export function growth(
  run: (size: number) => unknown,
  size: number,
  clock: Clock = () => performance.now(),
  timer: Clock = () => performance.now()
): number {
  const small = Math.floor(size / SCALE_FACTOR);
  run(small); // warm up: first-call costs (JIT, regex compilation, caches) belong to neither size
  run(size);
  const began = timer();
  let bestSmall = Infinity;
  let bestLarge = Infinity;
  let ratio = Infinity;
  for (let done = 1; done <= MAX_ROUNDS; done++) {
    bestSmall = Math.min(bestSmall, cost(run, small, clock), cost(run, small, clock));
    bestLarge = Math.min(bestLarge, cost(run, size, clock));
    ratio = bestSmall > 0 ? bestLarge / bestSmall : Infinity;
    if (done >= MIN_ROUNDS && (ratio < MAX_GROWTH || timer() - began > RETRY_BUDGET_MS)) break;
  }
  return ratio;
}

/** Fails when growing the input `SCALE_FACTOR` times grows the cost by more than `MAX_GROWTH`. */
export function expectLinear(
  run: (size: number) => unknown,
  size: number,
  clock: Clock = () => performance.now(),
  timer: Clock = () => performance.now()
): void {
  const ratio = growth(run, size, clock, timer);
  if (!(ratio < MAX_GROWTH)) {
    throw new Error(
      `${SCALE_FACTOR}x the input cost ${ratio.toFixed(1)}x the time: ` +
        `linear work is about ${SCALE_FACTOR}x, quadratic about ${SCALE_FACTOR ** 2}x`
    );
  }
}
