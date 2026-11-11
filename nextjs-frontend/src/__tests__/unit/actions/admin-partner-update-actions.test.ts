// @vitest-environment node
/**
 * applyPartnerUpdateAction and dismissPartnerUpdateAction: the admin gate runs first, the id, the review token and
 * the proposal hash are validated before the API is called, every failure is a curated message (never backend
 * text), and a success refreshes the admin pages (applying also refreshes the public page of a live offer).
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

const SECRET = 'SECRET-sk_live_abc123 postgres://user:pw@host/db';

const mocks = vi.hoisted(() => ({
  requireAdminSession: vi.fn(),
  applyAdminOfferPartnerUpdate: vi.fn(),
  dismissAdminOfferPartnerUpdate: vi.fn(),
  revalidatePath: vi.fn(),
  revalidateCatalogue: vi.fn(),
  logError: vi.fn(),
  logWarn: vi.fn(),
}));

vi.mock('next/cache', () => ({ revalidatePath: mocks.revalidatePath, revalidateTag: vi.fn() }));
vi.mock('@/lib/catalogue/revalidate', () => ({ revalidateCatalogue: mocks.revalidateCatalogue }));
vi.mock('@/lib/api/admin-guard', () => ({ requireAdminSession: mocks.requireAdminSession }));
vi.mock('@/lib/logger', () => ({
  logger: { info: vi.fn(), warn: mocks.logWarn, error: mocks.logError },
}));
vi.mock('@/lib/api/admin-offers', async () => {
  const actual =
    await vi.importActual<typeof import('@/lib/api/admin-offers')>('@/lib/api/admin-offers');
  return {
    ...actual,
    applyAdminOfferPartnerUpdate: mocks.applyAdminOfferPartnerUpdate,
    dismissAdminOfferPartnerUpdate: mocks.dismissAdminOfferPartnerUpdate,
  };
});

import {
  applyPartnerUpdateAction,
  dismissPartnerUpdateAction,
} from '@/app/actions/admin/offer-actions';
import { ADMIN_FORBIDDEN_MESSAGE } from '@/lib/api/admin-errors';
import {
  NO_PARTNER_UPDATE_MESSAGE,
  OFFER_CHANGED_MESSAGE,
  OFFER_NOT_FOUND_MESSAGE,
} from '@/lib/api/admin-catalogue-errors';

const ok = <T>(data: T) => ({ success: true as const, data });
const fail = (status?: number, code?: string, error = SECRET) => ({
  success: false as const,
  error,
  status,
  ...(code ? { code } : {}),
});

const TOKEN = 'tok-1_ABC';
const HASH = 'a'.repeat(64);
const OFFER = {
  id: 'o1',
  slug: 'free-coffee',
  status: 'PUBLISHED',
  review_token: 'f'.repeat(64),
  previous_review_token: 'e'.repeat(64),
};

const CASES = [
  ['apply', applyPartnerUpdateAction, mocks.applyAdminOfferPartnerUpdate, true],
  ['dismiss', dismissPartnerUpdateAction, mocks.dismissAdminOfferPartnerUpdate, false],
] as const;

beforeEach(() => {
  vi.clearAllMocks();
  mocks.requireAdminSession.mockResolvedValue({ ok: true });
});

describe.each(CASES)('%s partner update action', (_name, action, call, refreshesPublic) => {
  it('sends the token and the proposal hash, refreshes the admin pages and returns both tokens', async () => {
    call.mockResolvedValue(ok(OFFER));

    const result = await action('o1', TOKEN, HASH);

    expect(call).toHaveBeenCalledWith('o1', TOKEN, HASH);
    expect(result).toEqual({
      success: true,
      data: {
        id: 'o1',
        slug: 'free-coffee',
        status: 'PUBLISHED',
        reviewToken: 'f'.repeat(64),
        previousReviewToken: 'e'.repeat(64),
      },
    });
    expect(mocks.revalidatePath).toHaveBeenCalledWith('/admin/offers');
    expect(mocks.revalidatePath).toHaveBeenCalledWith('/admin/offers/o1');
    expect(mocks.revalidateCatalogue.mock.calls.length > 0).toBe(refreshesPublic);
  });

  it('refuses a non-admin before calling anything', async () => {
    mocks.requireAdminSession.mockResolvedValue({ ok: false, error: ADMIN_FORBIDDEN_MESSAGE });

    await expect(action('o1', TOKEN, HASH)).resolves.toEqual({
      success: false,
      error: ADMIN_FORBIDDEN_MESSAGE,
    });
    expect(call).not.toHaveBeenCalled();
  });

  it('turns a backend 403 into the curated refusal without a logged error', async () => {
    call.mockResolvedValue(fail(403));

    await expect(action('o1', TOKEN, HASH)).resolves.toEqual({
      success: false,
      error: ADMIN_FORBIDDEN_MESSAGE,
    });
    expect(mocks.logError).not.toHaveBeenCalled();
  });

  it('on 409 offer_changed says to review it again, with its reason, and refreshes', async () => {
    call.mockResolvedValue(fail(409, 'offer_changed'));

    const result = await action('o1', TOKEN, HASH);

    expect(result).toEqual({
      success: false,
      error: OFFER_CHANGED_MESSAGE,
      reason: 'offer_changed',
    });
    expect(mocks.revalidatePath).toHaveBeenCalledWith('/admin/offers/o1');
  });

  it('says nothing is waiting on 409 illegal_transition and refreshes', async () => {
    call.mockResolvedValue(fail(409, 'illegal_transition'));

    const result = await action('o1', TOKEN, HASH);

    expect(result).toEqual({ success: false, error: NO_PARTNER_UPDATE_MESSAGE });
    expect(mocks.revalidatePath).toHaveBeenCalledWith('/admin/offers/o1');
  });

  it('reports a missing offer with the curated message', async () => {
    call.mockResolvedValue(fail(404, 'not_found'));

    await expect(action('o1', TOKEN, HASH)).resolves.toEqual({
      success: false,
      error: OFFER_NOT_FOUND_MESSAGE,
    });
  });

  it('answers an unknown failure with a curated message and logs no backend text', async () => {
    call.mockResolvedValue(fail(500, undefined, SECRET));

    const result = await action('o1', TOKEN, HASH);

    expect(JSON.stringify(result)).not.toContain('SECRET');
    expect(JSON.stringify(mocks.logError.mock.calls)).not.toContain('SECRET');
    expect(mocks.revalidatePath).not.toHaveBeenCalled();
  });

  it.each(['', '../x', 'a b', 'x'.repeat(129)])('refuses the id %j before any call', async (id) => {
    await expect(action(id, TOKEN, HASH)).resolves.toEqual({
      success: false,
      error: OFFER_NOT_FOUND_MESSAGE,
    });
    expect(call).not.toHaveBeenCalled();
  });

  it.each([undefined, '', 'a b', '../x', 5, null])(
    'refuses the token %j before any call',
    async (token) => {
      await expect(action('o1', token as never, HASH)).resolves.toEqual({
        success: false,
        error: OFFER_CHANGED_MESSAGE,
      });
      expect(call).not.toHaveBeenCalled();
    }
  );

  it.each([undefined, '', 'g'.repeat(64), 'a'.repeat(63), 'A'.repeat(64), `${HASH}0`, 7])(
    'refuses the proposal hash %j before any call and logs only the action',
    async (hash) => {
      const result = await action('o1', TOKEN, hash as never);

      expect(result).toEqual({ success: false, error: OFFER_CHANGED_MESSAGE });
      expect(call).not.toHaveBeenCalled();
      expect(JSON.stringify(mocks.logWarn.mock.calls)).not.toContain(String(hash || 'zzz-none'));
    }
  );
});
