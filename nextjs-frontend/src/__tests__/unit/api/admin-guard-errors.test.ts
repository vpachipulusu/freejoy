// @vitest-environment node
/** The admin gate for server actions fails closed, and catalogue errors become curated text. */
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  verify: vi.fn(),
  resolveAdminAccess: vi.fn(),
  logError: vi.fn(),
}));

vi.mock('@/lib/auth/verified-session', async () => {
  const actual = await vi.importActual<typeof import('@/lib/auth/verified-session')>(
    '@/lib/auth/verified-session'
  );
  return { ...actual, verifyVerifiedSessionCookie: mocks.verify };
});
vi.mock('@/lib/api/admin-access', () => ({ resolveAdminAccess: mocks.resolveAdminAccess }));
vi.mock('@/lib/logger', () => ({
  logger: { error: mocks.logError, warn: vi.fn(), info: vi.fn() },
}));

import { requireAdminSession } from '@/lib/api/admin-guard';
import { EmailNotVerifiedError, INVALID_SESSION_CODE } from '@/lib/auth/verified-session';
import {
  ADMIN_FORBIDDEN_MESSAGE,
  ADMIN_REQUEST_FAILED_MESSAGE,
  ADMIN_UNAVAILABLE_MESSAGE,
} from '@/lib/api/admin-errors';
import {
  catalogueErrorMessage,
  INVALID_INPUT_MESSAGE,
  OFFER_NOT_FOUND_MESSAGE,
  publishBlockedReasons,
  RATE_LIMITED_MESSAGE,
} from '@/lib/api/admin-catalogue-errors';

const SECRET = 'SECRET postgres://user:pw@host/db sk_live_abc';

describe('requireAdminSession', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.verify.mockResolvedValue({ uid: 'u1', email: 'a@b.test', email_verified: true });
  });

  it('lets a verified admin through', async () => {
    mocks.resolveAdminAccess.mockResolvedValue({ status: 'admin', viewer: {} });
    await expect(requireAdminSession()).resolves.toEqual({ ok: true });
  });

  it.each([
    ['not-admin', 'not-admin'],
    ['session-rejected', 'rejected'],
  ])('refuses %s with the curated message and the reason %s', async (status, reason) => {
    mocks.resolveAdminAccess.mockResolvedValue({ status });
    await expect(requireAdminSession()).resolves.toEqual({
      ok: false,
      error: ADMIN_FORBIDDEN_MESSAGE,
      reason,
    });
  });

  it('refuses without a valid session and never asks the admin check', async () => {
    mocks.verify.mockRejectedValue(Object.assign(new Error('x'), { code: INVALID_SESSION_CODE }));
    await expect(requireAdminSession()).resolves.toEqual({
      ok: false,
      error: ADMIN_FORBIDDEN_MESSAGE,
      reason: 'no-session',
    });
    expect(mocks.resolveAdminAccess).not.toHaveBeenCalled();
  });

  it('refuses an unverified email address, with its own reason', async () => {
    mocks.verify.mockRejectedValue(new EmailNotVerifiedError());
    await expect(requireAdminSession()).resolves.toEqual({
      ok: false,
      error: ADMIN_FORBIDDEN_MESSAGE,
      reason: 'unverified',
    });
    expect(mocks.resolveAdminAccess).not.toHaveBeenCalled();
  });

  it('reports an outage as unavailable, not as a denial, and logs without backend text', async () => {
    mocks.verify.mockRejectedValue(new Error(SECRET));
    await expect(requireAdminSession()).resolves.toEqual({
      ok: false,
      error: ADMIN_UNAVAILABLE_MESSAGE,
      reason: 'unavailable',
    });
    mocks.verify.mockResolvedValue({ uid: 'u1' });
    mocks.resolveAdminAccess.mockResolvedValue({ status: 'error', message: SECRET });
    await expect(requireAdminSession()).resolves.toEqual({
      ok: false,
      error: ADMIN_UNAVAILABLE_MESSAGE,
      reason: 'unavailable',
    });
    expect(JSON.stringify(mocks.logError.mock.calls)).not.toContain('SECRET');
  });
});

describe('catalogueErrorMessage', () => {
  const fail = (status: number, code?: string, error = SECRET) => ({
    success: false as const,
    error,
    status,
    ...(code ? { code } : {}),
  });

  it.each([
    ['slug_taken', 409, 'That slug is already in use. Choose another.'],
    ['illegal_transition', 409, /not allowed for an offer in its current status/],
    ['invalid_url', 422, /https:\/\//],
    ['invalid_slug', 422, /lower-case/],
    ['invalid_postage', 422, /postage/],
    ['invalid_window', 422, /end date/],
    ['unknown_category', 422, /category/],
    ['unknown_merchant', 422, /merchant/],
  ])('maps the %s code to fixed wording', (code, status, expected) => {
    const message = catalogueErrorMessage(fail(status, code), OFFER_NOT_FOUND_MESSAGE);
    expect(message).not.toContain('SECRET');
    if (typeof expected === 'string') expect(message).toBe(expected);
    else expect(message).toMatch(expected);
  });

  it('names the unmet publish rules from the fixed table only', () => {
    const backend =
      'The offer cannot be published yet: its category is disabled; it has not been verified; <script>alert(1)</script>';
    const message = catalogueErrorMessage(fail(422, 'publish_blocked', backend), 'x');
    expect(message).toBe(
      'This offer cannot be published yet: its category is disabled; it has not been verified.'
    );
    expect(message).not.toContain('script');
  });

  it('names an unconfirmed commercial basis and points at the offer page', () => {
    const backend =
      'The offer cannot be published yet: its commercial basis has not been confirmed';

    expect(catalogueErrorMessage(fail(422, 'publish_blocked', backend), 'x')).toBe(
      'This offer cannot be published yet: its commercial basis has not been confirmed (open the offer and confirm it before publishing).'
    );
  });

  it('falls back to a plain message when no publish rule is recognised', () => {
    expect(catalogueErrorMessage(fail(422, 'publish_blocked', SECRET), 'x')).toBe(
      'This offer cannot be published yet.'
    );
    expect(publishBlockedReasons('something else')).toEqual([]);
  });

  it('maps statuses without a code', () => {
    expect(catalogueErrorMessage(fail(403), 'x')).toBe(ADMIN_FORBIDDEN_MESSAGE);
    expect(catalogueErrorMessage(fail(401), 'x')).toBe(ADMIN_FORBIDDEN_MESSAGE);
    expect(catalogueErrorMessage(fail(404, 'not_found'), 'Gone.')).toBe('Gone.');
    expect(catalogueErrorMessage(fail(422), 'x')).toBe(INVALID_INPUT_MESSAGE);
    expect(catalogueErrorMessage(fail(429), 'x')).toBe(RATE_LIMITED_MESSAGE);
    expect(catalogueErrorMessage(fail(409), 'x')).toBe(ADMIN_REQUEST_FAILED_MESSAGE);
    expect(catalogueErrorMessage(fail(500), 'x')).toBe(ADMIN_REQUEST_FAILED_MESSAGE);
    expect(catalogueErrorMessage({ success: false, error: SECRET }, 'x')).toBe(
      ADMIN_UNAVAILABLE_MESSAGE
    );
  });

  it('ignores an unknown code rather than echoing it', () => {
    expect(catalogueErrorMessage(fail(409, 'surprise_code'), 'x')).toBe(
      ADMIN_REQUEST_FAILED_MESSAGE
    );
  });
});
