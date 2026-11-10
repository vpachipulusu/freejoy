// @vitest-environment node
/**
 * The partner key wrappers call /v1/admin/merchants/{id}/partner-keys with the session cookie and the internal
 * key, send only the label, and map a key to a view that has no key value in it.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const cookieGet = vi.fn();
vi.mock('next/headers', () => ({
  cookies: vi.fn(async () => ({ get: cookieGet })),
  headers: vi.fn(async () => ({ get: vi.fn() })),
}));

import {
  createAdminPartnerKey,
  fetchAdminPartnerKeys,
  revokeAdminPartnerKey,
  toPartnerKeyView,
} from '@/lib/api/admin-partner-keys';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const KEY = {
  id: 'k'.repeat(24),
  merchant_id: 'm1',
  label: 'Feed',
  created_at: '2026-01-01T00:00:00Z',
  last_used_at: null,
  revoked_at: null,
};

describe('admin partner key wrappers', () => {
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

  it('lists a merchant keys with the session and the internal key', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ success: true, data: [KEY] }));

    const result = await fetchAdminPartnerKeys('m1');

    const request = fetchMock.mock.calls[0][0];
    expect(request.method).toBe('GET');
    expect(new URL(request.url).pathname).toBe('/v1/admin/merchants/m1/partner-keys');
    expect(request.headers.get('X-Internal-Key')).toBe('server-key');
    expect(request.headers.get('X-Session-Token')).toBe('firebase-session-cookie');
    expect(result).toEqual({ success: true, data: [KEY] });
  });

  it('creates a key by sending only the label and returns the key once', async () => {
    const apiKey = `fj_test_${'k'.repeat(24)}_${'0'.repeat(64)}`;
    fetchMock.mockResolvedValue(
      jsonResponse({ success: true, data: { key: KEY, api_key: apiKey } }, 201)
    );

    const result = await createAdminPartnerKey('m1', 'Feed');

    const request = fetchMock.mock.calls[0][0];
    expect(request.method).toBe('POST');
    expect(new URL(request.url).pathname).toBe('/v1/admin/merchants/m1/partner-keys');
    expect(await request.json()).toEqual({ label: 'Feed' });
    expect(result.success && result.data.api_key).toBe(apiKey);
  });

  it('revokes through its own route', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ success: true, data: KEY }));

    await revokeAdminPartnerKey('m1', 'key1');

    const request = fetchMock.mock.calls[0][0];
    expect(request.method).toBe('POST');
    expect(new URL(request.url).pathname).toBe('/v1/admin/merchants/m1/partner-keys/key1/revoke');
  });

  it('keeps the stable code of a refusal', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ success: false, error: 'x', code: 'merchant_no_website' }, 409)
    );

    const result = await createAdminPartnerKey('m1', 'Feed');

    expect(result).toMatchObject({ success: false, status: 409, code: 'merchant_no_website' });
  });

  it('maps a key to a view without any secret', () => {
    const view = toPartnerKeyView({ ...KEY, last_used_at: '2026-01-02T00:00:00Z' });

    expect(view).toEqual({
      id: KEY.id,
      label: 'Feed',
      createdAt: '2026-01-01T00:00:00Z',
      lastUsedAt: '2026-01-02T00:00:00Z',
      revokedAt: null,
    });
    expect(Object.keys(view)).not.toContain('apiKey');
  });
});
