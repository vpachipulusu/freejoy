import { beforeEach, describe, expect, it } from 'vitest';
import { NextRequest } from 'next/server';
import { proxy } from '@/proxy';
import { RATE_LIMIT_CONFIGS, resetRateLimitStore } from '@/lib/middleware/rate-limit';

const PATH = '/api/auth/session-status';
const call = (path: string, headers?: Record<string, string>) =>
  proxy(new NextRequest(`http://localhost${path}`, { headers }));

describe('proxy /api/auth/session-status', () => {
  beforeEach(() => resetRateLimitStore());

  it('lets signed-out callers reach it instead of answering 401', async () => {
    expect((await call(PATH)).status).toBe(200);
  });

  it('is not metered, so page loads never spend the sign-in (auth) budget', async () => {
    const calls = RATE_LIMIT_CONFIGS.auth.maxRequests + RATE_LIMIT_CONFIGS.api.maxRequests + 5;
    for (let i = 0; i < calls; i += 1) {
      expect((await call(PATH)).status).toBe(200);
    }
    // The auth bucket is untouched: a sibling /api/auth/ route still has its full budget.
    const sibling = await call('/api/auth/clear-session', { cookie: 'session=abc' });
    expect(sibling.status).toBe(200);
  });

  it('does not make look-alike paths public', async () => {
    expect((await call(`${PATH}/extra`)).status).toBe(401);
    expect((await call(`${PATH}-admin`)).status).toBe(401);
    expect((await call('/api/auth/other')).status).toBe(401);
  });
});
