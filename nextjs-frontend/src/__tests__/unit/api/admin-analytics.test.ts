// @vitest-environment node
/**
 * the admin analytics wrappers call the /v1/admin/analytics routes with the session cookie and the
 * internal key, and fold failures into the shared result envelope.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const cookieGet = vi.fn();
vi.mock('next/headers', () => ({
  cookies: vi.fn(async () => ({ get: cookieGet })),
  headers: vi.fn(async () => ({ get: vi.fn() })),
}));

import {
  fetchAdminOverview,
  fetchAdminRevenue,
  fetchAdminSystemStats,
  fetchMerchantClicks,
  syncAdminSubscriptions,
} from '@/lib/api/admin-analytics';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('admin analytics wrappers', () => {
  const fetchMock = vi.fn<(request: Request) => Promise<Response>>();

  beforeEach(() => {
    vi.stubEnv('BACKEND_API_URL', 'http://backend.test');
    vi.stubEnv('BACKEND_INTERNAL_KEY', 'server-key');
    cookieGet.mockReturnValue({ name: 'session', value: 'firebase-session-cookie' });
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    fetchMock.mockReset();
    cookieGet.mockReset();
  });

  it.each([
    ['overview', fetchAdminOverview, 'GET', '/v1/admin/analytics/overview'],
    ['revenue', fetchAdminRevenue, 'GET', '/v1/admin/analytics/revenue'],
    ['system stats', fetchAdminSystemStats, 'GET', '/v1/admin/analytics/system'],
    ['subscription sync', syncAdminSubscriptions, 'POST', '/v1/admin/analytics/subscription-sync'],
  ])(
    '%s calls the right route with the session headers and unwraps the data',
    async (_name, call, method, path) => {
      const data = { anything: 1 };
      fetchMock.mockResolvedValue(jsonResponse({ success: true, data }));

      const result = await call();

      expect(result).toEqual({ success: true, data });
      const request = fetchMock.mock.calls[0][0];
      expect(request.method).toBe(method);
      expect(new URL(request.url).pathname).toBe(path);
      expect(request.headers.get('X-Internal-Key')).toBe('server-key');
      expect(request.headers.get('X-Session-Token')).toBe('firebase-session-cookie');
    }
  );

  it('reports the backend status of a refusal', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ success: false, error: 'Forbidden' }, 403));

    const result = await fetchAdminRevenue();

    expect(result).toMatchObject({ success: false, status: 403 });
  });

  it('asks for one merchant and a date range and carries the stable code of a 404', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ success: false, error: 'Merchant not found', code: 'not_found' }, 404)
    );

    const result = await fetchMerchantClicks({
      merchantId: 'm1',
      from: '2026-03-10',
      to: '2026-03-31',
    });

    expect(result).toMatchObject({ success: false, status: 404, code: 'not_found' });
    const request = fetchMock.mock.calls[0][0];
    const url = new URL(request.url);
    expect(request.method).toBe('GET');
    expect(url.pathname).toBe('/v1/admin/analytics/clicks');
    expect(Object.fromEntries(url.searchParams)).toEqual({
      merchant_id: 'm1',
      from: '2026-03-10',
      to: '2026-03-31',
    });
    expect(request.headers.get('X-Session-Token')).toBe('firebase-session-cookie');
  });
});
