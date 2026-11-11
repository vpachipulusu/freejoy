// @vitest-environment node
/** GET /go/[offerId]: where it redirects, what it ignores, and what it never does. */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';

vi.mock('@/lib/logger', () => ({
  logger: { error: vi.fn(), warn: vi.fn(), info: vi.fn(), debug: vi.fn() },
}));
vi.mock('@/lib/api/outbound', () => ({ recordOutboundClick: vi.fn() }));
const cacheMocks = vi.hoisted(() => ({ revalidateTag: vi.fn(), revalidatePath: vi.fn() }));
vi.mock('next/cache', () => cacheMocks);

import { GET, HEAD } from '@/app/go/[offerId]/route';
import { recordOutboundClick, type OutboundClickResult } from '@/lib/api/outbound';
import { logger } from '@/lib/logger';

const recordMock = vi.mocked(recordOutboundClick);
const DESTINATION = 'https://www.awin1.com/cread.php?awinmid=1&awinaffid=2';

function call(path = '/go/offer_1', headers: Record<string, string> = {}, offerId = 'offer_1') {
  const request = new NextRequest(`http://localhost:3000${path}`, { headers });
  return GET(request, { params: Promise.resolve({ offerId }) });
}

function backendAnswers(result: OutboundClickResult) {
  recordMock.mockResolvedValue(result);
}

describe('GET /go/[offerId]', () => {
  beforeEach(() => {
    recordMock.mockReset();
    vi.clearAllMocks();
  });

  it('redirects with a 307 to the stored destination, uncached, with the referrer policy', async () => {
    backendAnswers({ kind: 'redirect', destination: DESTINATION });

    const response = await call();

    expect(response.status).toBe(307);
    expect(response.headers.get('Location')).toBe(DESTINATION);
    expect(response.headers.get('Cache-Control')).toBe('no-store');
    expect(response.headers.get('Referrer-Policy')).toBe('strict-origin-when-cross-origin');
  });

  it('sets no cookie on any outcome', async () => {
    const outcomes: OutboundClickResult[] = [
      { kind: 'redirect', destination: DESTINATION },
      { kind: 'ended', slug: 'free-shampoo' },
      { kind: 'disclosure_required', slug: 'free-shampoo' },
      { kind: 'not_found' },
      { kind: 'rate_limited', retryAfterSeconds: 60 },
      { kind: 'unavailable' },
    ];
    for (const outcome of outcomes) {
      backendAnswers(outcome);
      const response = await call('/go/offer_1', { cookie: 'session=abc; visitor=1' });
      expect(response.headers.get('Set-Cookie')).toBeNull();
      expect(response.cookies.getAll()).toEqual([]);
    }
  });

  it('sends the backend only the path id, the referrer host and a device class', async () => {
    backendAnswers({ kind: 'redirect', destination: DESTINATION });

    await call('/go/offer_1', {
      referer: 'https://News.Example/a/b?token=secret',
      'user-agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) Mobile/15E148',
      cookie: 'visitor=cookie-id-4242',
      'x-forwarded-for': '203.0.113.7',
      'x-client-ip': '203.0.113.8',
    });

    expect(recordMock).toHaveBeenCalledOnce();
    expect(recordMock).toHaveBeenCalledWith('offer_1', {
      referrerHost: 'news.example',
      userAgentClass: 'MOBILE',
    });
  });

  it.each([
    '?url=https://evil.example',
    '?to=//evil.example',
    '?redirect=//evil.example&next=javascript:alert(1)',
    '?destination=https%3A%2F%2Fevil.example',
    '?offerId=other&id=other',
    '?ended=1',
    '?x=%0d%0aLocation:%20https://evil.example',
  ])('ignores the query string %s', async (query) => {
    backendAnswers({ kind: 'redirect', destination: DESTINATION });

    const response = await call(`/go/offer_1${query}`);

    expect(response.headers.get('Location')).toBe(DESTINATION);
    expect(recordMock).toHaveBeenCalledWith('offer_1', expect.any(Object));
    expect(JSON.stringify(recordMock.mock.calls)).not.toContain('evil');
  });

  it.each([
    ['javascript', 'javascript:alert(1)'],
    ['data', 'data:text/html,<script>alert(1)</script>'],
    ['http', 'http://www.awin1.com/x'],
    ['protocol relative', '//evil.example/x'],
    ['userinfo', 'https://good.example@evil.example/'],
    ['backslash', 'https://evil.example\\@good.example/'],
    ['CRLF', 'https://www.awin1.com/x\r\nSet-Cookie: a=b'],
    ['scheme without slashes', 'https:evil.example'],
    ['custom port', 'https://www.awin1.com:8443/'],
    ['empty', ''],
  ])('never redirects to a %s destination even if the backend returned one', async (_n, value) => {
    backendAnswers({ kind: 'redirect', destination: value });

    const response = await call();

    expect(response.status).toBe(503);
    expect(response.headers.get('Location')).toBeNull();
    expect(response.headers.get('Cache-Control')).toBe('no-store');
    expect(logger.error).toHaveBeenCalledOnce();
    expect(JSON.stringify(vi.mocked(logger.error).mock.calls)).not.toContain('evil');
  });

  it('sends an ended offer to its page with a RELATIVE Location, ?ended=1 and no other parameter', async () => {
    backendAnswers({ kind: 'ended', slug: 'free-shampoo' });

    const response = await call('/go/offer_1?url=//evil.example&utm=x');

    expect(response.status).toBe(302);
    expect(response.headers.get('Location')).toBe('/freebies/free-shampoo?ended=1');
    expect(response.headers.get('Cache-Control')).toBe('no-store');
  });

  it('never puts a host in the ended Location, whatever Host or X-Forwarded-Host says', async () => {
    backendAnswers({ kind: 'ended', slug: 'free-shampoo' });

    const response = await call('/go/offer_1', {
      host: 'evil.example',
      'x-forwarded-host': 'evil.example',
      'x-forwarded-proto': 'https',
      forwarded: 'host=evil.example',
    });

    const location = response.headers.get('Location') ?? '';
    expect(location).toBe('/freebies/free-shampoo?ended=1');
    expect(location).not.toContain('evil');
    expect(location.startsWith('/') && !location.startsWith('//')).toBe(true);
  });

  it.each(['../admin', 'a/b', 'Free Shampoo', '//evil.example', 'x?y=1', ''])(
    'answers 404, not a redirect, when the ended slug is %j',
    async (slug) => {
      backendAnswers({ kind: 'ended', slug });

      const response = await call();

      expect(response.status).toBe(404);
      expect(response.headers.get('Location')).toBeNull();
    }
  );

  describe('the not-disclosed marker (?nd=1)', () => {
    it('forwards exactly nd=1 to the backend as commissionDisclosed false', async () => {
      backendAnswers({ kind: 'redirect', destination: DESTINATION });

      const response = await call('/go/offer_1?nd=1');

      expect(recordMock).toHaveBeenCalledWith('offer_1', {
        referrerHost: null,
        userAgentClass: 'OTHER',
        commissionDisclosed: false,
      });
      expect(response.headers.get('Location')).toBe(DESTINATION);
    });

    it('sends nothing extra for a plain link', async () => {
      backendAnswers({ kind: 'redirect', destination: DESTINATION });

      await call('/go/offer_1');

      expect(recordMock.mock.calls[0][1]).not.toHaveProperty('commissionDisclosed');
    });

    it.each([
      '?nd=0',
      '?nd=true',
      '?nd=',
      '?nd=1%20',
      '?nd=01',
      '?ND=1',
      '?nd=1&nd=1',
      '?nd=1&nd=0',
      '?nd[]=1',
    ])('treats %s as disclosed', async (query) => {
      backendAnswers({ kind: 'redirect', destination: DESTINATION });

      await call(`/go/offer_1${query}`);

      expect(recordMock.mock.calls[0][1]).not.toHaveProperty('commissionDisclosed');
    });

    it('answers a backend disclosure_required with a relative 303 to the offer page carrying ?ad=1', async () => {
      backendAnswers({ kind: 'disclosure_required', slug: 'free-shampoo' });

      const response = await call('/go/offer_1?nd=1&url=//evil.example', {
        host: 'evil.example',
        'x-forwarded-host': 'evil.example',
      });

      expect(response.status).toBe(303);
      expect(response.headers.get('Location')).toBe('/freebies/free-shampoo?ad=1');
      expect(response.headers.get('Cache-Control')).toBe('no-store');
      expect(response.headers.get('Set-Cookie')).toBeNull();
    });

    it.each(['../admin', 'a/b', 'Free Shampoo', '//evil.example', 'x?y=1', ''])(
      'answers 404, with no redirect, when the disclosure slug is %j',
      async (slug) => {
        backendAnswers({ kind: 'disclosure_required', slug });

        const response = await call('/go/offer_1?nd=1');

        expect(response.status).toBe(404);
        expect(response.headers.get('Location')).toBeNull();
      }
    );

    it('logs only the offer id and a reason, never a destination', async () => {
      backendAnswers({ kind: 'disclosure_required', slug: 'free-shampoo' });

      await call('/go/offer_1?nd=1');

      expect(JSON.stringify(vi.mocked(logger.info).mock.calls)).not.toMatch(/https?:/);
      expect(vi.mocked(logger.info).mock.calls[0][1]).toEqual({ offerId: 'offer_1' });
    });

    it('never touches the catalogue cache: no tag expiry or path revalidation on any outcome', async () => {
      const outcomes: OutboundClickResult[] = [
        { kind: 'redirect', destination: DESTINATION },
        { kind: 'disclosure_required', slug: 'free-shampoo' },
        { kind: 'ended', slug: 'free-shampoo' },
        { kind: 'not_found' },
      ];
      for (const outcome of outcomes) {
        backendAnswers(outcome);
        await call('/go/offer_1?nd=1');
      }

      expect(cacheMocks.revalidateTag).not.toHaveBeenCalled();
      expect(cacheMocks.revalidatePath).not.toHaveBeenCalled();
    });

    it('does not import the admin catalogue module', async () => {
      const { readFileSync } = await import('node:fs');
      const source = readFileSync('src/lib/outbound/respond.ts', 'utf8');

      expect(source).not.toMatch(
        /admin-catalogue-action|refreshPublicCatalogue|revalidateTag|revalidatePath|next\/cache/
      );
    });
  });

  it('answers an unknown or unpublished offer with an uncached 404', async () => {
    backendAnswers({ kind: 'not_found' });

    const response = await call();

    expect(response.status).toBe(404);
    expect(response.headers.get('Cache-Control')).toBe('no-store');
    expect(response.headers.get('Location')).toBeNull();
  });

  it('passes a rate limit on as 429 with the backend Retry-After', async () => {
    backendAnswers({ kind: 'rate_limited', retryAfterSeconds: 17 });

    const response = await call();

    expect(response.status).toBe(429);
    expect(response.headers.get('Retry-After')).toBe('17');
    expect(response.headers.get('Cache-Control')).toBe('no-store');
  });

  it('gives both 503 answers the same Retry-After', async () => {
    backendAnswers({ kind: 'unavailable' });
    const down = await call();
    backendAnswers({ kind: 'redirect', destination: 'javascript:alert(1)' });
    const refused = await call();

    expect(down.status).toBe(503);
    expect(refused.status).toBe(503);
    expect(down.headers.get('Retry-After')).toBe('30');
    expect(refused.headers.get('Retry-After')).toBe(down.headers.get('Retry-After'));
  });

  it('answers 503 when the backend is unavailable, with no backend text', async () => {
    backendAnswers({ kind: 'unavailable' });

    const response = await call();

    expect(response.status).toBe(503);
    expect(await response.text()).not.toMatch(/backend|error|stack/i);
    expect(response.headers.get('Location')).toBeNull();
  });

  it.each(['a b', '..', 'a.b', 'a;b', '%2e%2e', 'x'.repeat(65), '//evil.example', ''])(
    'answers 404 without calling the backend for the malformed id %j',
    async (offerId) => {
      const response = await call('/go/x', {}, offerId);

      expect(response.status).toBe(404);
      expect(recordMock).not.toHaveBeenCalled();
    }
  );

  it.each<Record<string, string>>([
    { 'sec-purpose': 'prefetch' },
    { purpose: 'prefetch' },
    { 'sec-purpose': 'prefetch;prerender' },
  ])('does not count a speculative load (%j)', async (headers) => {
    const response = await call('/go/offer_1', headers);

    expect(response.status).toBe(204);
    expect(response.headers.get('Location')).toBeNull();
    expect(recordMock).not.toHaveBeenCalled();
  });

  describe('Fetch Metadata', () => {
    it.each<Record<string, string>>([
      { 'sec-fetch-mode': 'no-cors', 'sec-fetch-dest': 'image' },
      { 'sec-fetch-mode': 'navigate', 'sec-fetch-dest': 'iframe' },
      { 'sec-fetch-mode': 'navigate', 'sec-fetch-dest': 'embed' },
      { 'sec-fetch-mode': 'cors', 'sec-fetch-dest': 'empty' },
      { 'sec-fetch-mode': 'same-origin', 'sec-fetch-dest': 'empty' },
      { 'sec-fetch-mode': 'no-cors', 'sec-fetch-dest': 'script' },
      { 'sec-fetch-mode': 'navigate', 'sec-fetch-dest': 'object' },
      { 'sec-fetch-mode': 'websocket' },
      { 'sec-fetch-dest': 'image' },
    ])('answers 204 uncached and never calls the backend for %j', async (headers) => {
      const response = await call('/go/offer_1', headers);

      expect(response.status).toBe(204);
      expect(response.headers.get('Cache-Control')).toBe('no-store');
      expect(response.headers.get('Location')).toBeNull();
      expect(recordMock).not.toHaveBeenCalled();
    });

    it.each<Record<string, string>>([
      { 'sec-fetch-mode': 'navigate', 'sec-fetch-dest': 'document' },
      { 'sec-fetch-mode': 'navigate' },
      { 'sec-fetch-dest': 'document' },
      {},
    ])('counts a top-level navigation or a client without the headers: %j', async (headers) => {
      backendAnswers({ kind: 'redirect', destination: DESTINATION });

      const response = await call('/go/offer_1', headers);

      expect(response.status).toBe(307);
      expect(recordMock).toHaveBeenCalledOnce();
    });
  });

  it('answers HEAD with 405 and never records a click', () => {
    const response = HEAD();

    expect(response.status).toBe(405);
    expect(response.headers.get('Allow')).toBe('GET');
    expect(recordMock).not.toHaveBeenCalled();
  });
});
