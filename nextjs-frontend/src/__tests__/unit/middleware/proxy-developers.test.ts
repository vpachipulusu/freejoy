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
