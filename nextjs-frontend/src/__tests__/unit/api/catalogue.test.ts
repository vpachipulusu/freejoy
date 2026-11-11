// @vitest-environment node
/** The catalogue wrapper: cache wiring, status mapping, timeout and the typed outage error. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  listOffers: vi.fn(),
  readOffer: vi.fn(),
  listCategories: vi.fn(),
  warn: vi.fn(),
  cookiesGet: vi.fn(),
  cacheCalls: [] as { keyParts: string[]; options: Record<string, unknown> }[],
  cachedWrappers: [] as ReturnType<typeof vi.fn>[],
}));

vi.mock('@/lib/api/generated', () => ({
  listOffers: mocks.listOffers,
  readOffer: mocks.readOffer,
  listCategories: mocks.listCategories,
}));
vi.mock('@/lib/logger', () => ({ logger: { warn: mocks.warn, error: vi.fn(), info: vi.fn() } }));
vi.mock('next/headers', () => ({
  cookies: vi.fn(async () => ({ get: mocks.cookiesGet })),
  headers: vi.fn(async () => ({ get: vi.fn() })),
}));
// A passthrough cache that records how it was configured and which wrapped reads ran.
vi.mock('next/cache', () => ({
  unstable_cache: (
    fn: (...args: unknown[]) => unknown,
    keyParts: string[],
    options: Record<string, unknown>
  ) => {
    mocks.cacheCalls.push({ keyParts, options });
    const wrapper = vi.fn((...args: unknown[]) => fn(...args));
    mocks.cachedWrappers.push(wrapper);
    return wrapper;
  },
}));

import {
  CATALOGUE_CACHE_TAG,
  CATALOGUE_REQUEST_TIMEOUT_MS,
  CATALOGUE_REVALIDATE_SECONDS,
  fetchCategories,
  fetchOffer,
  fetchOffers,
} from '@/lib/api/catalogue';
import { CatalogueRateLimitedError, CatalogueUnavailableError } from '@/lib/catalogue/errors';

// Captured at import, before any mockClear, because the caches are created once at module load.
const cacheCalls = [...mocks.cacheCalls];
const [offersCache, categoriesCache, offerCache] = [...mocks.cachedWrappers];

const ok = (data: unknown) => ({
  data: { success: true, data },
  response: { ok: true, status: 200 },
});
const failed = (status: number, body: unknown = { success: false, error: 'internal detail' }) => ({
  error: body,
  response: { ok: false, status },
});
const unreachable = () => ({ error: new TypeError('fetch failed') });

const OFFER = { id: 'o1', slug: 'free-tea' };
const LIST = { items: [OFFER], total: 1, limit: 12, offset: 0 };

beforeEach(() => {
  for (const mock of [mocks.listOffers, mocks.readOffer, mocks.listCategories, mocks.warn]) {
    mock.mockReset();
  }
  mocks.cookiesGet.mockReset();
  offersCache.mockClear();
  categoriesCache.mockClear();
  offerCache.mockClear();
  vi.stubEnv('BACKEND_API_URL', 'http://backend.test');
  vi.stubEnv('BACKEND_INTERNAL_KEY', 'server-key');
});

describe('cache wiring', () => {
  it('caches every read under the catalogue tag for 60 seconds', () => {
    expect(CATALOGUE_CACHE_TAG).toBe('catalogue');
    expect(CATALOGUE_REVALIDATE_SECONDS).toBe(60);
    expect(cacheCalls).toHaveLength(3);
    for (const call of cacheCalls) {
      expect(call.options).toEqual({ tags: ['catalogue'], revalidate: 60 });
      expect(call.keyParts[0]).toBe('catalogue');
    }
    expect(cacheCalls.map((call) => call.keyParts[1])).toEqual(['offers', 'categories', 'offer']);
  });

  it('serves a list without search text from the cache', async () => {
    mocks.listOffers.mockResolvedValue(ok(LIST));

    await expect(fetchOffers({ limit: 12, offset: 0 })).resolves.toEqual(LIST);

    expect(offersCache).toHaveBeenCalledTimes(1);
  });

  it('keeps a visitor’s search text out of the cache entirely', async () => {
    mocks.listOffers.mockResolvedValue(ok(LIST));

    await fetchOffers({ q: 'secret words', limit: 12, offset: 0 });

    expect(offersCache).not.toHaveBeenCalled();
    expect(mocks.listOffers).toHaveBeenCalledTimes(1);
  });
});

describe('fetchOffers', () => {
  it('sends only the filters that were set, in a stable order, with a timeout signal', async () => {
    mocks.listOffers.mockResolvedValue(ok(LIST));

    await fetchOffers({
      offset: 24,
      limit: 12,
      sort: 'a_z',
      postage: 'free',
      types: ['voucher', 'free_sample'],
      category: 'food-drink',
      featured: false,
    });

    const options = mocks.listOffers.mock.calls[0][0];
    expect(JSON.stringify(options.query)).toBe(
      JSON.stringify({
        category: 'food-drink',
        type: ['voucher', 'free_sample'],
        postage: 'free',
        featured: false,
        sort: 'a_z',
        limit: 12,
        offset: 24,
      })
    );
    expect(options.signal).toBeInstanceOf(AbortSignal);
  });

  it('omits empty filters', async () => {
    mocks.listOffers.mockResolvedValue(ok(LIST));

    await fetchOffers({ q: '', category: '', types: [] });

    expect(mocks.listOffers.mock.calls[0][0].query).toEqual({});
  });

  it('never forwards a session: the cookie jar is not read', async () => {
    mocks.listOffers.mockResolvedValue(ok(LIST));
    mocks.cookiesGet.mockReturnValue({ value: 'visitor-session' });

    await fetchOffers();

    expect(mocks.cookiesGet).not.toHaveBeenCalled();
  });

  it.each([
    ['a 500', failed(500)],
    ['a 429', failed(429, { success: false, error: 'Rate limit exceeded' })],
    ['a 401', failed(401)],
    ['a 422', failed(422)],
    ['an unreachable backend', unreachable()],
  ])('throws the typed outage error on %s and logs why', async (_name, result) => {
    mocks.listOffers.mockResolvedValue(result);

    const attempt = fetchOffers({ q: 'private search', limit: 12, offset: 0 });

    await expect(attempt).rejects.toBeInstanceOf(CatalogueUnavailableError);
    expect(mocks.warn).toHaveBeenCalledTimes(1);
    expect(mocks.warn.mock.calls[0][0]).toBe('Catalogue read failed');
    expect(JSON.stringify(mocks.warn.mock.calls)).not.toContain('private search');
  });

  it('never lets backend text reach the thrown error', async () => {
    mocks.listOffers.mockResolvedValue(failed(500, { success: false, error: 'psycopg boom' }));

    const error = await fetchOffers().catch((caught: Error) => caught);

    expect(error).toBeInstanceOf(CatalogueUnavailableError);
    expect((error as Error).message).toBe('Catalogue unavailable');
    expect(String(error)).not.toContain('psycopg');
  });

  it('throws the outage error when the call itself throws', async () => {
    mocks.listOffers.mockRejectedValue(new Error('socket hang up'));

    await expect(fetchOffers()).rejects.toBeInstanceOf(CatalogueUnavailableError);
  });

  it('gives up after the request timeout', async () => {
    const controller = new AbortController();
    const timeout = vi.spyOn(AbortSignal, 'timeout').mockReturnValue(controller.signal);
    mocks.listOffers.mockImplementation(
      ({ signal }: { signal: AbortSignal }) =>
        new Promise((_resolve, reject) => {
          signal.addEventListener('abort', () =>
            reject(new DOMException('The operation timed out', 'TimeoutError'))
          );
        })
    );

    const attempt = fetchOffers();
    await vi.waitFor(() => expect(mocks.listOffers).toHaveBeenCalled());
    controller.abort();

    await expect(attempt).rejects.toBeInstanceOf(CatalogueUnavailableError);
    expect(timeout).toHaveBeenCalledWith(CATALOGUE_REQUEST_TIMEOUT_MS);
    expect(CATALOGUE_REQUEST_TIMEOUT_MS).toBe(3_000);
    timeout.mockRestore();
  });
});

describe('fetchCategories', () => {
  it('returns the categories through the cache', async () => {
    mocks.listCategories.mockResolvedValue(ok([{ slug: 'pets' }]));

    await expect(fetchCategories()).resolves.toEqual([{ slug: 'pets' }]);
    expect(categoriesCache).toHaveBeenCalledTimes(1);
    expect(mocks.listCategories.mock.calls[0][0].signal).toBeInstanceOf(AbortSignal);
  });

  it('throws the outage error on a failure', async () => {
    mocks.listCategories.mockResolvedValue(failed(503));

    await expect(fetchCategories()).rejects.toBeInstanceOf(CatalogueUnavailableError);
  });
});

describe('slugs that are not slugs', () => {
  it.each(['../admin', 'Bad Slug', 'a/b', '', 'tea?x=1'])(
    'answers not found for %j without calling the backend',
    async (slug) => {
      await expect(fetchOffer(slug)).resolves.toEqual({ kind: 'not_found' });

      expect(mocks.readOffer).not.toHaveBeenCalled();
      expect(offerCache).not.toHaveBeenCalled();
    }
  );

  it('drops a category filter that is not a slug instead of sending it', async () => {
    mocks.listOffers.mockResolvedValue(ok(LIST));

    await fetchOffers({ category: '../etc', limit: 12 });
    await fetchOffers({ category: 'pets', limit: 12 });

    expect(mocks.listOffers.mock.calls[0][0].query).toEqual({ limit: 12 });
    expect(mocks.listOffers.mock.calls[1][0].query).toEqual({ category: 'pets', limit: 12 });
  });
});

describe('fetchOffer cache age bound', () => {
  const NOW = new Date('2026-11-11T12:00:00Z');
  const STALE_OFFER = { id: 'old', slug: 'free-tea' };

  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(NOW);
  });

  afterEach(() => vi.useRealTimers());

  const cachedAt = (ageSeconds: number) => ({
    at: NOW.getTime() - ageSeconds * 1000,
    result: { kind: 'found', offer: STALE_OFFER },
  });

  it('serves a fresh cache entry without asking the backend', async () => {
    offerCache.mockResolvedValueOnce(cachedAt(30));

    await expect(fetchOffer('free-tea')).resolves.toEqual({ kind: 'found', offer: STALE_OFFER });
    expect(mocks.readOffer).not.toHaveBeenCalled();
  });

  it('still serves an entry exactly as old as the revalidate window', async () => {
    offerCache.mockResolvedValueOnce(cachedAt(CATALOGUE_REVALIDATE_SECONDS));

    await expect(fetchOffer('free-tea')).resolves.toEqual({ kind: 'found', offer: STALE_OFFER });
    expect(mocks.readOffer).not.toHaveBeenCalled();
  });

  it('bypasses an entry older than the window and reads the backend directly', async () => {
    offerCache.mockResolvedValueOnce(cachedAt(CATALOGUE_REVALIDATE_SECONDS + 1));
    mocks.readOffer.mockResolvedValue(ok(OFFER));

    await expect(fetchOffer('free-tea')).resolves.toEqual({ kind: 'found', offer: OFFER });
    expect(mocks.readOffer).toHaveBeenCalledOnce();
  });

  it('stamps a new entry with the time it was read, and keeps the result inside', async () => {
    mocks.readOffer.mockResolvedValue(ok(OFFER));

    await fetchOffer('free-tea');

    // The cached function is the stamping loader: its value is { at, result }.
    const loader = mocks.cacheCalls.findIndex(
      (call) => call.keyParts.join('/') === 'catalogue/offer'
    );
    expect(loader).toBeGreaterThanOrEqual(0);
    await expect(offerCache.mock.results[0].value).resolves.toEqual({
      at: NOW.getTime(),
      result: { kind: 'found', offer: OFFER },
    });
  });

  it('a backend failure on the bypass is the usual typed outage', async () => {
    offerCache.mockResolvedValueOnce(cachedAt(CATALOGUE_REVALIDATE_SECONDS + 5));
    mocks.readOffer.mockResolvedValue(failed(500));

    await expect(fetchOffer('free-tea')).rejects.toBeInstanceOf(CatalogueUnavailableError);
  });
});

describe('fetchOffer', () => {
  it('returns a found offer and asks the API for that slug', async () => {
    mocks.readOffer.mockResolvedValue(ok(OFFER));

    await expect(fetchOffer('free-tea')).resolves.toEqual({ kind: 'found', offer: OFFER });
    expect(mocks.readOffer.mock.calls[0][0].path).toEqual({ slug: 'free-tea' });
    expect(offerCache).toHaveBeenCalledWith('free-tea');
  });

  it('maps a 404 to not found, a result the cache may keep', async () => {
    mocks.readOffer.mockResolvedValue(
      failed(404, { success: false, error: 'Offer not found', code: 'offer_not_found' })
    );

    await expect(fetchOffer('nope')).resolves.toEqual({ kind: 'not_found' });
    expect(mocks.warn).not.toHaveBeenCalled();
  });

  it('throws for a 404 whose body is not the backend envelope (an HTML page from a proxy, say)', async () => {
    mocks.readOffer.mockResolvedValue(failed(404, '<html>Not Found</html>'));

    await expect(fetchOffer('nope')).rejects.toBeInstanceOf(CatalogueUnavailableError);
    expect(mocks.warn).toHaveBeenCalledTimes(1);
  });

  it.each([
    ['the route-level "Not Found" with no code', { success: false, error: 'Not Found' }],
    ['the offer message without the code', { success: false, error: 'Offer not found' }],
    ['another code', { success: false, error: 'x', code: 'something_else' }],
    ['the right code without the envelope', { code: 'offer_not_found' }],
  ])('throws for a 404 with %s: never branch on the text', async (_name, body) => {
    mocks.readOffer.mockResolvedValue(failed(404, body));

    await expect(fetchOffer('nope')).rejects.toBeInstanceOf(CatalogueUnavailableError);
  });

  it('throws for a 404 with no body at all', async () => {
    mocks.readOffer.mockResolvedValue({ response: { ok: false, status: 404 } });

    await expect(fetchOffer('nope')).rejects.toBeInstanceOf(CatalogueUnavailableError);
  });

  it('throws a rate-limited error, still an unavailable error, for a 429', async () => {
    mocks.readOffer.mockResolvedValue(failed(429, { success: false, error: 'slow down' }));

    const error = await fetchOffer('free-tea').catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(CatalogueRateLimitedError);
    expect(error).toBeInstanceOf(CatalogueUnavailableError);
  });

  it('maps a 410 to the ended stub', async () => {
    const stub = {
      slug: 'old-tea',
      title: 'Old tea',
      category: { slug: 'food-drink', name: 'Food & drink' },
      ended_at: '2026-05-31T23:59:00Z',
    };
    mocks.readOffer.mockResolvedValue(
      failed(410, { success: false, error: 'This offer has ended', expired: true, data: stub })
    );

    await expect(fetchOffer('old-tea')).resolves.toEqual({ kind: 'gone', ended: stub });
  });

  it('keeps a missing end date on the 410 stub as null', async () => {
    const stub = {
      slug: 'old-tea',
      title: 'Old tea',
      category: { slug: 'food-drink', name: 'Food & drink' },
      ended_at: null,
    };
    mocks.readOffer.mockResolvedValue(failed(410, { success: false, expired: true, data: stub }));

    const result = await fetchOffer('old-tea');

    expect(result).toEqual({ kind: 'gone', ended: stub });
  });

  it.each([
    ['no data', { success: false, expired: true }],
    [
      'no title',
      { success: false, expired: true, data: { slug: 'x', category: { slug: 'a', name: 'A' } } },
    ],
    ['no category', { success: false, expired: true, data: { slug: 'x', title: 'X' } }],
    [
      'a category without a name',
      { success: false, expired: true, data: { slug: 'x', title: 'X', category: { slug: 'a' } } },
    ],
  ])('treats a backend 410 with %s as ended with an unknown stub', async (_name, body) => {
    mocks.readOffer.mockResolvedValue({ error: body, response: { ok: false, status: 410 } });

    await expect(fetchOffer('x')).resolves.toEqual({ kind: 'gone', ended: null });
  });

  it.each([
    ['an HTML page', '<html>Gone</html>'],
    ['an empty body', ''],
    ['no body', undefined],
    ['an empty object', {}],
    ['an envelope that does not say expired', { success: false, error: 'Gone' }],
    ['expired: false', { success: false, expired: false }],
    ['an expired flag without the envelope', { expired: true }],
  ])(
    'throws for a 410 with %s: only the backend ended envelope is an ended offer',
    async (_name, body) => {
      mocks.readOffer.mockResolvedValue({
        error: body ?? '',
        response: { ok: false, status: 410 },
      });

      await expect(fetchOffer('x')).rejects.toBeInstanceOf(CatalogueUnavailableError);
      expect(mocks.warn).toHaveBeenCalledTimes(1);
    }
  );

  it.each([
    ['a 500', failed(500)],
    ['a 429', failed(429)],
    ['an unreachable backend', unreachable()],
  ])('throws the outage error on %s, never a not-found', async (_name, result) => {
    mocks.readOffer.mockResolvedValue(result);

    await expect(fetchOffer('free-tea')).rejects.toBeInstanceOf(CatalogueUnavailableError);
    expect(mocks.warn).toHaveBeenCalledTimes(1);
  });
});
