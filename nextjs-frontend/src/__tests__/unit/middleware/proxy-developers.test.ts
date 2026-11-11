import { beforeEach, describe, expect, it } from 'vitest';
import { NextRequest } from 'next/server';
import { proxy } from '@/proxy';
import { resetRateLimitStore } from '@/lib/middleware/rate-limit';

const call = (path: string) => proxy(new NextRequest(`http://localhost${path}`));

/** The partner API documentation and its downloadable document are public: no sign-in is asked for. */
describe.each(['/developers/partner-api', '/partner-openapi.json'])('proxy %s', (path) => {
  beforeEach(() => resetRateLimitStore());

  it('is public: a visitor with no session is not redirected to log in', async () => {
    const response = await call(path);

    expect(response.status).toBe(200);
    expect(response.headers.get('location')).toBeNull();
  });

  it('still gets the security headers', async () => {
    const response = await call(path);

    expect(response.headers.get('x-content-type-options')).toBe('nosniff');
  });
});

/** The page says `noindex` in its own metadata; the static file cannot, so the proxy says it in a header. */
describe('proxy and search engines', () => {
  beforeEach(() => resetRateLimitStore());

  it('marks the downloadable API description noindex', async () => {
    const response = await call('/partner-openapi.json');

    expect(response.headers.get('x-robots-tag')).toBe('noindex');
  });

  it('leaves the pages alone: they carry their own robots metadata', async () => {
    expect((await call('/developers/partner-api')).headers.get('x-robots-tag')).toBeNull();
    expect((await call('/partners')).headers.get('x-robots-tag')).toBeNull();
  });
});
