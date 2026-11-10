// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  RATE_LIMIT_MAX_ENTRIES,
  rateLimit,
  rateLimitStoreSize,
  resetRateLimitStore,
} from '@/lib/middleware/rate-limit';

const CONFIG = { scope: 'budget', maxRequests: 10, windowMs: 60_000 };

// 100,000 distinct keys are slow under coverage on a busy machine.
vi.setConfig({ testTimeout: 30_000 });

beforeEach(() => resetRateLimitStore());
afterEach(() => vi.useRealTimers());

describe('rateLimit with a cost', () => {
  it('charges one token by default, as it always did', () => {
    for (let call = 0; call < 10; call += 1) expect(rateLimit('a', CONFIG).success).toBe(true);

    expect(rateLimit('a', CONFIG).success).toBe(false);
  });

  it('charges the cost in one go and refuses a call that does not fit', () => {
    expect(rateLimit('a', { ...CONFIG, cost: 6 }).success).toBe(true);
    const refused = rateLimit('a', { ...CONFIG, cost: 5 });

    expect(refused.success).toBe(false);
    expect(refused.remaining).toBe(0);
  });

  it('does not take tokens for a refused call: a smaller one still fits', () => {
    rateLimit('a', { ...CONFIG, cost: 6 });
    rateLimit('a', { ...CONFIG, cost: 5 });

    expect(rateLimit('a', { ...CONFIG, cost: 4 }).success).toBe(true);
    expect(rateLimit('a', CONFIG).success).toBe(false);
  });

  it('lets a one-unit call through a bucket that a big call has not emptied', () => {
    rateLimit('a', { ...CONFIG, cost: 3 });

    expect(rateLimit('a', CONFIG).remaining).toBe(6);
  });

  it('always refuses a cost bigger than the whole budget', () => {
    expect(rateLimit('a', { ...CONFIG, cost: 11 }).success).toBe(false);
  });

  it('keeps separate buckets per client and per scope', () => {
    rateLimit('a', { ...CONFIG, cost: 10 });

    expect(rateLimit('b', CONFIG).success).toBe(true);
    expect(rateLimit('a', { ...CONFIG, scope: 'other' }).success).toBe(true);
  });

  it('refills with time, by the cost too', () => {
    vi.useFakeTimers();
    rateLimit('a', { ...CONFIG, cost: 10 });
    expect(rateLimit('a', { ...CONFIG, cost: 5 }).success).toBe(false);

    vi.advanceTimersByTime(30_000);

    expect(rateLimit('a', { ...CONFIG, cost: 5 }).success).toBe(true);
  });
});

describe('rateLimit store size', () => {
  it('drops an entry once its bucket would be full again (it carries no state)', () => {
    vi.useFakeTimers();
    rateLimit('a', CONFIG);
    expect(rateLimitStoreSize()).toBe(1);

    vi.advanceTimersByTime(CONFIG.windowMs);
    rateLimit('b', CONFIG);

    expect(rateLimitStoreSize()).toBe(1);
  });

  it('forgets a spent bucket after a full window, and gives the client a whole one', () => {
    vi.useFakeTimers();
    rateLimit('a', { ...CONFIG, cost: 10 });

    vi.advanceTimersByTime(CONFIG.windowMs);

    expect(rateLimit('a', { ...CONFIG, cost: 10 }).success).toBe(true);
  });

  it('never grows past the cap, however many distinct keys arrive', () => {
    for (let key = 0; key < RATE_LIMIT_MAX_ENTRIES * 2 + 1; key += 1)
      rateLimit(`key-${key}`, CONFIG);

    expect(rateLimitStoreSize()).toBeLessThanOrEqual(RATE_LIMIT_MAX_ENTRIES);
  });

  it('holds the cap at about 50,000 entries', () => {
    expect(RATE_LIMIT_MAX_ENTRIES).toBe(50_000);
  });

  it('evicts the oldest bucket first, so a client that keeps calling keeps its state', () => {
    rateLimit('keeper', { ...CONFIG, cost: 10 });
    for (let key = 0; key < RATE_LIMIT_MAX_ENTRIES + 10; key += 1) {
      rateLimit(`filler-${key}`, CONFIG);
      if (key % 1000 === 0) rateLimit('keeper', { ...CONFIG, cost: 0 });
    }

    expect(rateLimit('keeper', CONFIG).success).toBe(false);
  });
});
