// @vitest-environment node
/**
 * The partner key server actions: the admin gate runs first, input is validated, failures become curated
 * messages (never backend text), and the key value is returned once and appears in no log.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

const SECRET = 'SECRET-sk_live_abc123 postgres://user:pw@host/db';
const API_KEY = `fj_test_${'k'.repeat(24)}_${'0'.repeat(64)}`;
const KEY_ID = 'k'.repeat(24);

const mocks = vi.hoisted(() => ({
  requireAdminSession: vi.fn(),
  createAdminPartnerKey: vi.fn(),
  revokeAdminPartnerKey: vi.fn(),
  revalidatePath: vi.fn(),
  logError: vi.fn(),
  logWarn: vi.fn(),
}));

vi.mock('next/cache', () => ({ revalidatePath: mocks.revalidatePath, revalidateTag: vi.fn() }));
vi.mock('@/lib/catalogue/revalidate', () => ({ revalidateCatalogue: vi.fn() }));
vi.mock('@/lib/api/admin-guard', () => ({ requireAdminSession: mocks.requireAdminSession }));
vi.mock('@/lib/logger', () => ({
  logger: { info: vi.fn(), warn: mocks.logWarn, error: mocks.logError },
}));
vi.mock('@/lib/api/admin-partner-keys', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api/admin-partner-keys')>(
    '@/lib/api/admin-partner-keys'
  );
  return {
    ...actual,
    createAdminPartnerKey: mocks.createAdminPartnerKey,
    revokeAdminPartnerKey: mocks.revokeAdminPartnerKey,
  };
});

import {
  createPartnerKeyAction,
  revokePartnerKeyAction,
} from '@/app/actions/admin/partner-key-actions';
import { ADMIN_FORBIDDEN_MESSAGE } from '@/lib/api/admin-errors';
import {
  INVALID_FORM_MESSAGE,
  PARTNER_KEY_NOT_FOUND_MESSAGE,
  RATE_LIMITED_MESSAGE,
} from '@/lib/api/admin-catalogue-errors';

const RECORD = {
  id: KEY_ID,
  merchant_id: 'm1',
  label: 'Feed',
  created_at: '2026-01-01T00:00:00Z',
  last_used_at: null,
  revoked_at: null,
};
const fail = (status?: number, code?: string) => ({
  success: false as const,
  error: SECRET,
  status,
  ...(code ? { code } : {}),
});

beforeEach(() => {
  vi.clearAllMocks();
  mocks.requireAdminSession.mockResolvedValue({ ok: true });
});

describe('createPartnerKeyAction', () => {
  it('returns the key once, with its view, and refreshes the merchant page', async () => {
    mocks.createAdminPartnerKey.mockResolvedValue({
      success: true,
      data: { key: RECORD, api_key: API_KEY },
    });

    const result = await createPartnerKeyAction('m1', { label: ' Feed ' });

    expect(mocks.createAdminPartnerKey).toHaveBeenCalledWith('m1', 'Feed');
    expect(result).toEqual({
      success: true,
      data: {
        apiKey: API_KEY,
        key: {
          id: KEY_ID,
          label: 'Feed',
          createdAt: RECORD.created_at,
          lastUsedAt: null,
          revokedAt: null,
        },
      },
    });
    expect(mocks.revalidatePath).toHaveBeenCalledWith('/admin/merchants/m1');
  });

  it('refuses a non-admin before calling anything', async () => {
    mocks.requireAdminSession.mockResolvedValue({ ok: false, error: ADMIN_FORBIDDEN_MESSAGE });

    await expect(createPartnerKeyAction('m1', { label: 'x' })).resolves.toEqual({
      success: false,
      error: ADMIN_FORBIDDEN_MESSAGE,
    });
    expect(mocks.createAdminPartnerKey).not.toHaveBeenCalled();
  });

  it('answers a bad label with a field message and does not call the backend', async () => {
    const result = await createPartnerKeyAction('m1', { label: '' });

    expect(result).toMatchObject({ success: false, error: INVALID_FORM_MESSAGE });
    expect(result.success === false && result.fieldErrors?.label).toBeTruthy();
    expect(mocks.createAdminPartnerKey).not.toHaveBeenCalled();
  });

  it('refuses a malformed merchant id without a backend call', async () => {
    const result = await createPartnerKeyAction('../x', { label: 'Feed' });

    expect(result).toEqual({ success: false, error: PARTNER_KEY_NOT_FOUND_MESSAGE });
    expect(mocks.createAdminPartnerKey).not.toHaveBeenCalled();
  });

  it.each([
    ['merchant_no_website', 409, 'Add the merchant website before issuing a key.'],
    ['merchant_inactive', 409, 'Activate the merchant before issuing a key.'],
    ['keys_unavailable', 503, 'Partner keys cannot be issued in this environment.'],
  ])('maps the code %s to a curated message, never backend text', async (code, status, message) => {
    mocks.createAdminPartnerKey.mockResolvedValue(fail(status, code));

    const result = await createPartnerKeyAction('m1', { label: 'Feed' });

    expect(result).toEqual({ success: false, error: message });
    expect(JSON.stringify(result)).not.toContain(SECRET);
  });

  it('puts a refused key-looking label on the label field', async () => {
    mocks.createAdminPartnerKey.mockResolvedValue(fail(422, 'invalid_label'));

    const result = await createPartnerKeyAction('m1', { label: 'Feed' });

    expect(result).toEqual({
      success: false,
      error: 'A label is a name, not a key. Do not paste a key.',
      fieldErrors: { label: 'A label is a name, not a key. Do not paste a key.' },
    });
  });

  it.each([
    ['a 403', fail(403), 'You do not have permission to do that'],
    ['a 404', fail(404, 'not_found'), PARTNER_KEY_NOT_FOUND_MESSAGE],
    ['an uncoded 409', fail(409), 'Something went wrong. Please try again.'],
    ['an uncoded 422', fail(422), 'Some values were rejected. Check the form and try again.'],
    [
      'an unreachable backend',
      fail(undefined),
      'The admin service is unavailable. Please try again later.',
    ],
  ])('maps %s on create to a curated message', async (_name, outcome, message) => {
    mocks.createAdminPartnerKey.mockResolvedValue(outcome);

    await expect(createPartnerKeyAction('m1', { label: 'Feed' })).resolves.toEqual({
      success: false,
      error: message,
    });
  });

  it('maps a rate limit to the curated message', async () => {
    mocks.createAdminPartnerKey.mockResolvedValue(fail(429));

    await expect(createPartnerKeyAction('m1', { label: 'Feed' })).resolves.toEqual({
      success: false,
      error: RATE_LIMITED_MESSAGE,
    });
  });

  it('logs an unexpected failure by status and code only, never the label or the backend text', async () => {
    mocks.createAdminPartnerKey.mockResolvedValue(fail(500));

    await createPartnerKeyAction('m1', { label: 'Feed' });

    const logged = JSON.stringify([...mocks.logError.mock.calls, ...mocks.logWarn.mock.calls]);
    expect(logged).not.toContain(SECRET);
    expect(logged).not.toContain('Feed');
  });

  it('never logs the key after a success', async () => {
    mocks.createAdminPartnerKey.mockResolvedValue({
      success: true,
      data: { key: RECORD, api_key: API_KEY },
    });

    await createPartnerKeyAction('m1', { label: 'Feed' });

    const logged = JSON.stringify([...mocks.logError.mock.calls, ...mocks.logWarn.mock.calls]);
    expect(logged).not.toContain(API_KEY);
  });
});

describe('revokePartnerKeyAction', () => {
  it('revokes and returns the view', async () => {
    mocks.revokeAdminPartnerKey.mockResolvedValue({
      success: true,
      data: { ...RECORD, revoked_at: '2026-01-02T00:00:00Z' },
    });

    const result = await revokePartnerKeyAction('m1', KEY_ID);

    expect(mocks.revokeAdminPartnerKey).toHaveBeenCalledWith('m1', KEY_ID);
    expect(result).toMatchObject({ success: true, data: { revokedAt: '2026-01-02T00:00:00Z' } });
    expect(mocks.revalidatePath).toHaveBeenCalledWith('/admin/merchants/m1');
  });

  it('refuses a non-admin before calling anything', async () => {
    mocks.requireAdminSession.mockResolvedValue({ ok: false, error: ADMIN_FORBIDDEN_MESSAGE });

    await expect(revokePartnerKeyAction('m1', KEY_ID)).resolves.toEqual({
      success: false,
      error: ADMIN_FORBIDDEN_MESSAGE,
    });
    expect(mocks.revokeAdminPartnerKey).not.toHaveBeenCalled();
  });

  it.each([
    ['merchant', '../x', KEY_ID],
    ['key', 'm1', 'a b'],
  ])('refuses a malformed %s id without a backend call', async (_name, merchantId, keyId) => {
    await expect(revokePartnerKeyAction(merchantId, keyId)).resolves.toEqual({
      success: false,
      error: PARTNER_KEY_NOT_FOUND_MESSAGE,
    });
    expect(mocks.revokeAdminPartnerKey).not.toHaveBeenCalled();
  });

  it.each([
    ['a 403', fail(403), 'You do not have permission to do that'],
    ['a 429', fail(429), RATE_LIMITED_MESSAGE],
    ['a 500', fail(500), 'Something went wrong. Please try again.'],
  ])('maps %s on revoke to a curated message', async (_name, outcome, message) => {
    mocks.revokeAdminPartnerKey.mockResolvedValue(outcome);

    await expect(revokePartnerKeyAction('m1', KEY_ID)).resolves.toEqual({
      success: false,
      error: message,
    });
  });

  it('maps a 404 to the curated not-found message', async () => {
    mocks.revokeAdminPartnerKey.mockResolvedValue(fail(404, 'not_found'));

    await expect(revokePartnerKeyAction('m1', KEY_ID)).resolves.toEqual({
      success: false,
      error: PARTNER_KEY_NOT_FOUND_MESSAGE,
    });
  });
});
