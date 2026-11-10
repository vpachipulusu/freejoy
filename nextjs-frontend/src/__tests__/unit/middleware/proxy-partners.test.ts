import { beforeEach, describe, expect, it } from 'vitest';
import { NextRequest } from 'next/server';
import { proxy } from '@/proxy';
import { resetRateLimitStore } from '@/lib/middleware/rate-limit';

const call = (path: string) => proxy(new NextRequest(`http://localhost${path}`));

/** The proxy keeps no list of public routes: a path is public unless it matches a protected prefix. */
describe('proxy /partners', () => {
  beforeEach(() => resetRateLimitStore());

  it('is public: a visitor with no session is not redirected to log in', async () => {
    const response = await call('/partners');

    expect(response.status).toBe(200);
    expect(response.headers.get('location')).toBeNull();
  });

  it('still gets the security headers', async () => {
    const response = await call('/partners');

    expect(response.headers.get('x-content-type-options')).toBe('nosniff');
  });

  it('leaves the protected areas protected', async () => {
    expect((await call('/dashboard')).status).toBe(307);
  });
});
