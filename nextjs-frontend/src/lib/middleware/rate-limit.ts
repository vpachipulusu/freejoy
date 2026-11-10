/**
 * Rate Limiting Middleware
 *
 * Implements token bucket algorithm for rate limiting
 * Stores rate limit data in memory (use Redis in production)
 */

interface RateLimitStore {
  tokens: number;
  lastRefill: number;
  /** Kept so a sweep can tell, without the config, when this bucket would be full again. */
  windowMs: number;
}

const rateLimitStore = new Map<string, RateLimitStore>();

/**
 * Most buckets held at once. A key per visitor address would otherwise grow without bound (anyone can send
 * requests from many addresses); past the cap the least recently used bucket goes. A bucket that is evicted
 * simply starts full again, which is what it would have done after one idle window anyway.
 */
export const RATE_LIMIT_MAX_ENTRIES = 50_000;

export interface RateLimitConfig {
  maxRequests: number;
  windowMs: number;
  message?: string;
  /** Names the bucket: the same client has a separate token bucket per scope (auth, api, general). */
  scope?: string;
  /** Tokens this call takes (default 1). A call that cannot afford it is refused and takes nothing. */
  cost?: number;
}

export interface RateLimitResult {
  success: boolean;
  limit: number;
  remaining: number;
  reset: number;
  message?: string;
}

/** Whether a bucket last refilled a whole window ago: it is full again and holds no state worth keeping. */
function isFullAgain(store: RateLimitStore, now: number): boolean {
  return now - store.lastRefill >= store.windowMs;
}

/** Evict this many oldest buckets at once when the cap is hit: a Map scans its deleted slots on every restart. */
const EVICTION_BATCH = 1_000;
/** How often (at most) full-again buckets are swept from the old end. */
const SWEEP_EVERY_MS = 1_000;
let lastSweep = 0;

/** Room for one more bucket: drop full-again ones from the old end now and then, and the oldest at the cap. */
function makeRoom(now: number): void {
  if (now - lastSweep >= SWEEP_EVERY_MS || rateLimitStore.size >= RATE_LIMIT_MAX_ENTRIES) {
    lastSweep = now;
    for (const [key, store] of rateLimitStore) {
      if (!isFullAgain(store, now)) break;
      rateLimitStore.delete(key);
    }
  }
  if (rateLimitStore.size < RATE_LIMIT_MAX_ENTRIES) return;
  let evicted = 0;
  for (const key of rateLimitStore.keys()) {
    rateLimitStore.delete(key);
    evicted += 1;
    if (evicted >= EVICTION_BATCH) break;
  }
}

/** The bucket for `key`, fresh if there is none or it is full again; most recently used goes last. */
function takeBucket(
  key: string,
  now: number,
  maxRequests: number,
  windowMs: number
): RateLimitStore {
  const existing = rateLimitStore.get(key);
  if (existing && !isFullAgain(existing, now)) {
    rateLimitStore.delete(key);
    rateLimitStore.set(key, existing);
    return existing;
  }
  rateLimitStore.delete(key);
  makeRoom(now);
  const created: RateLimitStore = { tokens: maxRequests, lastRefill: now, windowMs };
  rateLimitStore.set(key, created);
  return created;
}

/** Number of buckets held. For tests and monitoring. */
export function rateLimitStoreSize(): number {
  return rateLimitStore.size;
}

/**
 * Token bucket rate limiter
 * @param identifier - Unique identifier (e.g., IP address, user ID)
 * @param config - Rate limit configuration
 */
export function rateLimit(identifier: string, config: RateLimitConfig): RateLimitResult {
  const now = Date.now();
  const { maxRequests, windowMs, message } = config;
  const cost = config.cost ?? 1;
  // One bucket per scope and client, so traffic to one kind of route cannot spend another's tokens.
  const key = `${config.scope ?? 'default'}:${identifier}`;

  const store = takeBucket(key, now, maxRequests, windowMs);

  // Calculate tokens to add based on time elapsed
  const timePassed = now - store.lastRefill;
  const refillRate = maxRequests / windowMs;
  const tokensToAdd = Math.floor(timePassed * refillRate);

  // Refill tokens
  if (tokensToAdd > 0) {
    store.tokens = Math.min(maxRequests, store.tokens + tokensToAdd);
    store.lastRefill = now;
  }

  // Calculate reset time
  const reset = now + windowMs;

  // Check if request is allowed
  if (store.tokens >= cost) {
    store.tokens -= cost;
    return {
      success: true,
      limit: maxRequests,
      remaining: Math.floor(store.tokens),
      reset,
    };
  }

  // Rate limit exceeded
  return {
    success: false,
    limit: maxRequests,
    remaining: 0,
    reset,
    message: message || 'Too many requests. Please try again later.',
  };
}

/**
 * Clean up old entries from the rate limit store
 * Should be called periodically to prevent memory leaks
 */
export function cleanupRateLimitStore(maxAge: number = 3600000): void {
  const now = Date.now();

  for (const [key, store] of rateLimitStore.entries()) {
    if (now - store.lastRefill > maxAge) {
      rateLimitStore.delete(key);
    }
  }
}

/** Empties every bucket. For tests only: production never calls it. */
export function resetRateLimitStore(): void {
  rateLimitStore.clear();
  lastSweep = 0;
}

/**
 * Get rate limit headers for response
 */
export function getRateLimitHeaders(result: RateLimitResult): Record<string, string> {
  return {
    'X-RateLimit-Limit': result.limit.toString(),
    'X-RateLimit-Remaining': result.remaining.toString(),
    'X-RateLimit-Reset': new Date(result.reset).toISOString(),
  };
}

/**
 * Preset configurations for common endpoints
 */
export const RATE_LIMIT_CONFIGS = {
  // Strict limits for auth endpoints
  auth: {
    scope: 'auth',
    maxRequests: 5,
    windowMs: 15 * 60 * 1000, // 15 minutes
    message: 'Too many authentication attempts. Please try again in 15 minutes.',
  },
  // Standard API limits
  api: {
    scope: 'api',
    maxRequests: 100,
    windowMs: 60 * 1000, // 1 minute
    message: 'API rate limit exceeded. Please slow down.',
  },
  // General limits
  general: {
    scope: 'general',
    maxRequests: 200,
    windowMs: 60 * 1000, // 1 minute
    message: 'Too many requests. Please try again later.',
  },
} as const;

// Cleanup interval (every hour)
if (typeof window === 'undefined') {
  setInterval(() => cleanupRateLimitStore(), 3600000);
}
