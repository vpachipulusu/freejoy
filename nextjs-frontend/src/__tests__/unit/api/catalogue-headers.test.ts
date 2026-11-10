// @vitest-environment node
/**
 * What the catalogue wrapper actually puts on the wire: the internal key, never the visitor's session,
 * and the visitor address only from the platform's trusted header.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  cookiesGet: vi.fn(),
  headerValues: new Map<string, string>(),
  warn: vi.fn(),
  cacheArgs: [] as unknown[][],
}));

vi.mock('@/lib/logger', () => ({
  logger: { warn: mocks.warn, error: vi.fn(), info: vi.fn() },
}));
vi.mock('next/headers', () => ({
  cookies: vi.fn(async () => ({ get: mocks.cookiesGet })),
  headers: vi.fn(async () => ({ get: (name: string) => mocks.headerValues.get(name) ?? null })),
}));
// Like Next.js, run the cached function inside its own AsyncLocalStorage context, and record the
// arguments, which are what the real cache hashes into its key.
vi.mock('next/cache', async () => {
  const { AsyncLocalStorage } = await import('node:async_hooks');
  const nextContext = new AsyncLocalStorage<string>();
  return {
    unstable_cache:
      (fn: (...args: unknown[]) => unknown) =>
      (...args: unknown[]) => {
        mocks.cacheArgs.push(args);
        return nextContext.run('unstable-cache', () => fn(...args));
      },
  };
});

import { fetchCategories, fetchOffer, fetchOffers } from '@/lib/api/catalogue';

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

const fetchMock = vi.fn<(request: Request) => Promise<Response>>();
const sentRequest = (index = 0) => fetchMock.mock.calls[index][0];

beforeEach(() => {
  vi.stubEnv('BACKEND_API_URL', 'http://backend.test');
  vi.stubEnv('BACKEND_INTERNAL_KEY', 'server-key');
  vi.stubGlobal('fetch', fetchMock);
  fetchMock.mockImplementation(async () =>
    json({ success: true, data: { items: [], total: 0, limit: 12, offset: 0 } })
  );
  mocks.cookiesGet.mockReturnValue({ value: 'visitor-session' });
  mocks.headerValues.clear();
  mocks.cacheArgs.length = 0;
  mocks.warn.mockClear();
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  fetchMock.mockReset();
  mocks.cookiesGet.mockReset();
});

describe('catalogue requests', () => {
  it('sends the internal key and no session, even when the visitor has a session cookie', async () => {
    await fetchOffers({ limit: 12, offset: 0 });

    const request = sentRequest();
    expect(request.url).toBe('http://backend.test/v1/catalogue/offers?limit=12&offset=0');
    expect(request.headers.get('X-Internal-Key')).toBe('server-key');
    expect(request.headers.get('X-Session-Token')).toBeNull();
    expect(request.headers.get('cookie')).toBeNull();
    expect(mocks.cookiesGet).not.toHaveBeenCalled();
  });

  it('repeats the type parameter once per type', async () => {
    await fetchOffers({ types: ['free_sample', 'voucher'], postage: 'free' });

    const url = new URL(sentRequest().url);
    expect(url.searchParams.getAll('type')).toEqual(['free_sample', 'voucher']);
    expect(url.searchParams.get('postage')).toBe('free');
  });

  it('reads one offer and the categories from their own paths without a session', async () => {
    fetchMock.mockImplementation(async () => json({ success: true, data: [] }));
    await fetchCategories();
    fetchMock.mockImplementation(async () => json({ success: true, data: { slug: 'free-tea' } }));
    await fetchOffer('free-tea');

    expect(new URL(sentRequest(0).url).pathname).toBe('/v1/catalogue/categories');
    expect(new URL(sentRequest(1).url).pathname).toBe('/v1/catalogue/offers/free-tea');
    expect(sentRequest(0).headers.get('X-Session-Token')).toBeNull();
    expect(sentRequest(1).headers.get('X-Session-Token')).toBeNull();
    expect(mocks.cookiesGet).not.toHaveBeenCalled();
  });
});

describe('the visitor address and the cache', () => {
  it('survives the cache boundary but is never part of the cache key', async () => {
    vi.stubEnv('VERCEL', '1');
    mocks.headerValues.set('x-vercel-forwarded-for', '203.0.113.7');

    await fetchOffers({ category: 'pets', limit: 12, offset: 0 });
    await fetchOffer('free-tea');
    await fetchCategories();

    for (let index = 0; index < 3; index += 1) {
      expect(sentRequest(index).headers.get('X-Client-IP')).toBe('203.0.113.7');
    }
    expect(mocks.cacheArgs).toEqual([
      [{ category: 'pets', limit: 12, offset: 0 }],
      ['free-tea'],
      [],
    ]);
    expect(JSON.stringify(mocks.cacheArgs)).not.toContain('203.0.113.7');
  });

  it('does not leak one visitor’s address into the next read', async () => {
    vi.stubEnv('VERCEL', '1');
    mocks.headerValues.set('x-vercel-forwarded-for', '203.0.113.7');
    await fetchOffers();
    mocks.headerValues.delete('x-vercel-forwarded-for');
    await fetchOffers({ limit: 5 });

    expect(sentRequest(0).headers.get('X-Client-IP')).toBe('203.0.113.7');
    expect(sentRequest(1).headers.get('X-Client-IP')).toBeNull();
  });
});

describe('the visitor address', () => {
  it('forwards the platform address as X-Client-IP on Vercel', async () => {
    vi.stubEnv('VERCEL', '1');
    mocks.headerValues.set('x-vercel-forwarded-for', ' 203.0.113.7 ');

    await fetchOffers();

    expect(sentRequest().headers.get('X-Client-IP')).toBe('203.0.113.7');
  });

  it('forwards an IPv6 address too', async () => {
    vi.stubEnv('VERCEL', '1');
    mocks.headerValues.set('x-vercel-forwarded-for', '2001:db8::1');

    await fetchCategories();

    expect(sentRequest().headers.get('X-Client-IP')).toBe('2001:db8::1');
  });

  it('never takes the address from a header the client can set', async () => {
    vi.stubEnv('VERCEL', '1');
    mocks.headerValues.set('x-forwarded-for', '198.51.100.9');
    mocks.headerValues.set('x-real-ip', '198.51.100.9');
    mocks.headerValues.set('x-client-ip', '198.51.100.9');

    await fetchOffers();

    expect(sentRequest().headers.get('X-Client-IP')).toBeNull();
  });

  it('forwards the one configured trusted header off Vercel', async () => {
    vi.stubEnv('TRUSTED_CLIENT_IP_HEADER', 'X-Real-IP');
    mocks.headerValues.set('x-real-ip', '203.0.113.20');
    mocks.headerValues.set('x-forwarded-for', '198.51.100.1');

    await fetchOffers();

    expect(sentRequest().headers.get('X-Client-IP')).toBe('203.0.113.20');
  });

  it('refuses the e2e test header in an ordinary build, even when it is configured', async () => {
    vi.stubEnv('TRUSTED_CLIENT_IP_HEADER', 'x-e2e-client-ip');
    mocks.headerValues.set('x-e2e-client-ip', '10.1.2.3');
    mocks.headerValues.set('host', 'localhost:3000');

    await fetchOffers();

    expect(sentRequest().headers.get('X-Client-IP')).toBeNull();
  });

  it.each([
    ['the mode switch is off', { E2E_RATE_LIMIT_TEST_MODE: '0', NEXT_DIST_DIR: '.next-e2e' }, 'localhost:3000'],
    ['the build is not the e2e folder', { E2E_RATE_LIMIT_TEST_MODE: '1', NEXT_DIST_DIR: '.next' }, 'localhost:3000'],
    ['it runs on Vercel', { E2E_RATE_LIMIT_TEST_MODE: '1', NEXT_DIST_DIR: '.next-e2e', VERCEL_ENV: 'preview' }, 'localhost:3000'],
    ['the host is not local', { E2E_RATE_LIMIT_TEST_MODE: '1', NEXT_DIST_DIR: '.next-e2e' }, 'freejoy.example'],
  ])('refuses the e2e test header when %s', async (_name, env, host) => {
    for (const [name, value] of Object.entries(env)) vi.stubEnv(name, value);
    vi.stubEnv('TRUSTED_CLIENT_IP_HEADER', 'x-e2e-client-ip');
    mocks.headerValues.set('x-e2e-client-ip', '10.1.2.3');
    mocks.headerValues.set('host', host);

    await fetchOffers();

    expect(sentRequest().headers.get('X-Client-IP')).toBeNull();
  });

  it('accepts the e2e test header only in a local e2e build, the same test the edge limiter uses', async () => {
    vi.stubEnv('E2E_RATE_LIMIT_TEST_MODE', '1');
    vi.stubEnv('NEXT_DIST_DIR', '.next-e2e');
    vi.stubEnv('TRUSTED_CLIENT_IP_HEADER', 'x-e2e-client-ip');
    mocks.headerValues.set('x-e2e-client-ip', '10.1.2.3');
    mocks.headerValues.set('host', 'localhost:3000');

    await fetchOffers();

    expect(sentRequest().headers.get('X-Client-IP')).toBe('10.1.2.3');
  });

  it('prefers the Vercel header, then falls back to the configured one', async () => {
    vi.stubEnv('VERCEL', '1');
    vi.stubEnv('TRUSTED_CLIENT_IP_HEADER', 'x-real-ip');
    mocks.headerValues.set('x-vercel-forwarded-for', '203.0.113.7');
    mocks.headerValues.set('x-real-ip', '203.0.113.20');
    await fetchOffers();
    expect(sentRequest(0).headers.get('X-Client-IP')).toBe('203.0.113.7');

    mocks.headerValues.delete('x-vercel-forwarded-for');
    await fetchCategories();
    expect(sentRequest(1).headers.get('X-Client-IP')).toBe('203.0.113.20');
  });

  it('omits it when neither a Vercel header nor a configured header applies', async () => {
    mocks.headerValues.set('x-vercel-forwarded-for', '203.0.113.7');
    mocks.headerValues.set('x-real-ip', '203.0.113.20');

    await fetchOffers();

    expect(sentRequest().headers.get('X-Client-IP')).toBeNull();
  });

  it.each([
    ['a value that is not an address', 'not-an-ip'],
    ['an address list', '203.0.113.20, 198.51.100.1'],
    ['a scoped IPv6 address', 'fe80::1%eth0'],
    ['a bracketed IPv6 address', '[2001:db8::1]'],
  ])('ignores the configured header when it holds %s', async (_name, value) => {
    vi.stubEnv('TRUSTED_CLIENT_IP_HEADER', 'x-real-ip');
    mocks.headerValues.set('x-real-ip', value);

    await fetchOffers();

    expect(sentRequest().headers.get('X-Client-IP')).toBeNull();
  });

  it.each(['x real ip', 'x-real-ip, x-evil', 'x_real_ip', '../x', ''])(
    'ignores a configured header name that is not a header name: %j',
    async (name) => {
      vi.stubEnv('TRUSTED_CLIENT_IP_HEADER', name);
      mocks.headerValues.set(name.trim().toLowerCase(), '203.0.113.20');
      mocks.headerValues.set('x-real-ip', '203.0.113.20');

      await fetchOffers();

      expect(sentRequest().headers.get('X-Client-IP')).toBeNull();
    }
  );

  it.each(['x-forwarded-for', 'Forwarded', 'via', 'x-vercel-forwarded-for'])(
    'never trusts %s as the configured header, and says so once',
    async (name) => {
      vi.stubEnv('TRUSTED_CLIENT_IP_HEADER', name);
      mocks.headerValues.set(name.toLowerCase(), '198.51.100.1');

      await fetchOffers();
      await fetchOffers({ limit: 5 });

      expect(sentRequest(0).headers.get('X-Client-IP')).toBeNull();
      expect(sentRequest(1).headers.get('X-Client-IP')).toBeNull();
      const warnings = mocks.warn.mock.calls.filter(([message]) =>
        String(message).includes('TRUSTED_CLIENT_IP_HEADER')
      );
      expect(warnings).toHaveLength(1);
    }
  );

  it('still uses the Vercel header automatically when the denylisted one is configured', async () => {
    vi.stubEnv('VERCEL', '1');
    vi.stubEnv('TRUSTED_CLIENT_IP_HEADER', 'x-forwarded-for');
    mocks.headerValues.set('x-vercel-forwarded-for', '203.0.113.7');
    mocks.headerValues.set('x-forwarded-for', '198.51.100.1');

    await fetchOffers();

    expect(sentRequest().headers.get('X-Client-IP')).toBe('203.0.113.7');
  });

  it('does not let a spoofed x-forwarded-for win over the configured header', async () => {
    vi.stubEnv('TRUSTED_CLIENT_IP_HEADER', 'x-real-ip');
    mocks.headerValues.set('x-forwarded-for', '198.51.100.1');
    mocks.headerValues.set('x-real-ip', '203.0.113.20');

    await fetchOffers();

    expect(sentRequest().headers.get('X-Client-IP')).toBe('203.0.113.20');
  });

  it('omits it off Vercel, where no header can be trusted', async () => {
    mocks.headerValues.set('x-vercel-forwarded-for', '203.0.113.7');

    await fetchOffers();

    expect(sentRequest().headers.get('X-Client-IP')).toBeNull();
  });

  it.each([
    ['absent', undefined],
    ['a scoped IPv6 address', 'fe80::1%eth0'],
    ['not an address', 'not-an-ip'],
    ['an injected header', '203.0.113.7\r\nX-Internal-Key: stolen'],
  ])('omits it when the platform header is %s', async (_name, value) => {
    vi.stubEnv('VERCEL', '1');
    if (value !== undefined) mocks.headerValues.set('x-vercel-forwarded-for', value);

    await fetchOffers();

    expect(sentRequest().headers.get('X-Client-IP')).toBeNull();
  });

  it('omits it without the internal key, as every other forwarded header', async () => {
    vi.stubEnv('VERCEL', '1');
    vi.stubEnv('BACKEND_INTERNAL_KEY', '');
    mocks.headerValues.set('x-vercel-forwarded-for', '203.0.113.7');

    await fetchOffers();

    expect(sentRequest().headers.get('X-Client-IP')).toBeNull();
  });

  it('does not break a read made outside a request scope', async () => {
    vi.stubEnv('VERCEL', '1');
    const { headers } = await import('next/headers');
    vi.mocked(headers).mockRejectedValueOnce(new Error('outside a request scope'));

    await expect(fetchOffers()).resolves.toMatchObject({ total: 0 });

    expect(sentRequest().headers.get('X-Client-IP')).toBeNull();
  });
});
