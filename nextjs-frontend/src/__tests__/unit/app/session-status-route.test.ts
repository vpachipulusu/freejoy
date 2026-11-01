import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ cookieValue: undefined as string | undefined }));

vi.mock('next/headers', () => ({
  cookies: async () => ({
    get: (name: string) =>
      name === 'session' && mocks.cookieValue !== undefined
        ? { name, value: mocks.cookieValue }
        : undefined,
  }),
}));

import { GET } from '@/app/api/auth/session-status/route';

describe('GET /api/auth/session-status', () => {
  beforeEach(() => {
    mocks.cookieValue = undefined;
  });

  it('reports signedIn true when the session cookie is present', async () => {
    mocks.cookieValue = 'opaque-cookie-value';
    const response = await GET();

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ signedIn: true });
  });

  it('reports signedIn false when there is no session cookie', async () => {
    const response = await GET();

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ signedIn: false });
  });

  it('treats an empty session cookie as signed out', async () => {
    mocks.cookieValue = '';
    expect(await (await GET()).json()).toEqual({ signedIn: false });
  });

  it('is never cached and never echoes the cookie or any user data', async () => {
    mocks.cookieValue = 'opaque-cookie-value';
    const response = await GET();

    expect(response.headers.get('Cache-Control')).toBe('no-store');
    const text = await response.clone().text();
    expect(text).not.toContain('opaque-cookie-value');
    expect(Object.keys(await response.json())).toEqual(['signedIn']);
  });
});
