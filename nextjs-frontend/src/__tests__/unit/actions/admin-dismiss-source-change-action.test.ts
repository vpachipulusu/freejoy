// @vitest-environment node
/**
 * dismissSourceChangeAction: the admin gate runs first, the id and the review token are validated before the API
 * is called, every failure is a curated message (never backend text), and a success refreshes the list and the
 * offer page (the public catalogue is untouched: a dismissal changes nothing a visitor sees).
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

const SECRET = 'SECRET-sk_live_abc123 postgres://user:pw@host/db';

const mocks = vi.hoisted(() => ({
  requireAdminSession: vi.fn(),
  dismissAdminOfferSourceChange: vi.fn(),
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
  return { ...actual, dismissAdminOfferSourceChange: mocks.dismissAdminOfferSourceChange };
});

import { dismissSourceChangeAction } from '@/app/actions/admin/offer-actions';
import { ADMIN_FORBIDDEN_MESSAGE, ADMIN_UNAVAILABLE_MESSAGE } from '@/lib/api/admin-errors';
import {
  NO_SOURCE_CHANGE_MESSAGE,
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
const OFFER = {
  id: 'o1',
  slug: 'free-coffee',
  status: 'DRAFT',
  review_token: 'f'.repeat(64),
  previous_review_token: 'e'.repeat(64),
};

beforeEach(() => {
  vi.clearAllMocks();
  mocks.requireAdminSession.mockResolvedValue({ ok: true });
});

describe('dismissSourceChangeAction', () => {
  it('dismisses with the review token, refreshes the list and the page, and returns both tokens', async () => {
    mocks.dismissAdminOfferSourceChange.mockResolvedValue(ok(OFFER));

    const result = await dismissSourceChangeAction('o1', TOKEN);

    expect(mocks.dismissAdminOfferSourceChange).toHaveBeenCalledWith('o1', TOKEN);
    expect(result).toEqual({
      success: true,
      data: {
        id: 'o1',
        slug: 'free-coffee',
        status: 'DRAFT',
        reviewToken: 'f'.repeat(64),
        previousReviewToken: 'e'.repeat(64),
      },
    });
    expect(mocks.revalidatePath).toHaveBeenCalledWith('/admin/offers');
    expect(mocks.revalidatePath).toHaveBeenCalledWith('/admin/offers/o1');
    expect(mocks.revalidateCatalogue).not.toHaveBeenCalled();
  });

  it('leaves out a token the API sent that is not made of plain characters', async () => {
    mocks.dismissAdminOfferSourceChange.mockResolvedValue(
      ok({ ...OFFER, review_token: 'not plain!', previous_review_token: undefined })
    );

    const result = await dismissSourceChangeAction('o1', TOKEN);

    expect(result).toMatchObject({ success: true, data: { id: 'o1' } });
    expect(result).not.toHaveProperty('data.reviewToken', expect.anything());
    expect(JSON.stringify(result)).not.toContain('not plain');
  });

  it('refuses a non-admin before calling anything', async () => {
    mocks.requireAdminSession.mockResolvedValue({ ok: false, error: ADMIN_FORBIDDEN_MESSAGE });

    await expect(dismissSourceChangeAction('o1', TOKEN)).resolves.toEqual({
      success: false,
      error: ADMIN_FORBIDDEN_MESSAGE,
    });

    expect(mocks.dismissAdminOfferSourceChange).not.toHaveBeenCalled();
    expect(mocks.revalidatePath).not.toHaveBeenCalled();
  });

  it('reports an unavailable admin check without writing', async () => {
    mocks.requireAdminSession.mockResolvedValue({ ok: false, error: ADMIN_UNAVAILABLE_MESSAGE });

    await expect(dismissSourceChangeAction('o1', TOKEN)).resolves.toEqual({
      success: false,
      error: ADMIN_UNAVAILABLE_MESSAGE,
    });
    expect(mocks.dismissAdminOfferSourceChange).not.toHaveBeenCalled();
  });

  it('turns a backend 403 (no offers.publish permission) into the curated refusal, not logged as an error', async () => {
    mocks.dismissAdminOfferSourceChange.mockResolvedValue(fail(403));

    const result = await dismissSourceChangeAction('o1', TOKEN);

    expect(result).toEqual({ success: false, error: ADMIN_FORBIDDEN_MESSAGE });
    expect(mocks.revalidatePath).not.toHaveBeenCalled();
    expect(mocks.logError).not.toHaveBeenCalled();
  });

  it('on 409 offer_changed shows the review-again message with its reason and refreshes the pages', async () => {
    mocks.dismissAdminOfferSourceChange.mockResolvedValue(fail(409, 'offer_changed'));

    const result = await dismissSourceChangeAction('o1', TOKEN);

    expect(result).toEqual({
      success: false,
      error: OFFER_CHANGED_MESSAGE,
      reason: 'offer_changed',
    });
    expect(JSON.stringify(result)).not.toContain('SECRET');
    expect(mocks.revalidatePath).toHaveBeenCalledWith('/admin/offers');
    expect(mocks.revalidatePath).toHaveBeenCalledWith('/admin/offers/o1');
    expect(mocks.revalidateCatalogue).not.toHaveBeenCalled();
  });

  it('says there is nothing to dismiss when the API sees no marker, and refreshes so the page catches up', async () => {
    mocks.dismissAdminOfferSourceChange.mockResolvedValue(fail(409, 'illegal_transition'));

    const result = await dismissSourceChangeAction('o1', TOKEN);

    expect(result).toEqual({ success: false, error: NO_SOURCE_CHANGE_MESSAGE });
    expect(NO_SOURCE_CHANGE_MESSAGE).not.toContain('SECRET');
    expect(mocks.revalidatePath).toHaveBeenCalledWith('/admin/offers/o1');
  });

  it('reports a missing offer with the curated not-found message', async () => {
    mocks.dismissAdminOfferSourceChange.mockResolvedValue(fail(404, 'not_found'));

    await expect(dismissSourceChangeAction('o1', TOKEN)).resolves.toEqual({
      success: false,
      error: OFFER_NOT_FOUND_MESSAGE,
    });
  });

  it('answers an unknown failure with a curated message and logs no backend text', async () => {
    mocks.dismissAdminOfferSourceChange.mockResolvedValue(fail(500, undefined, SECRET));

    const result = await dismissSourceChangeAction('o1', TOKEN);

    expect(result.success).toBe(false);
    expect(JSON.stringify(result)).not.toContain('SECRET');
    expect(mocks.logError).toHaveBeenCalled();
    expect(JSON.stringify(mocks.logError.mock.calls)).not.toContain('SECRET');
    expect(mocks.revalidatePath).not.toHaveBeenCalled();
  });

  it.each(['', '../x', 'a b', 'x'.repeat(129), 'o1/../o2'])(
    'refuses the id %j before any backend call',
    async (id) => {
      const result = await dismissSourceChangeAction(id, TOKEN);

      expect(result).toEqual({ success: false, error: OFFER_NOT_FOUND_MESSAGE });
      expect(mocks.dismissAdminOfferSourceChange).not.toHaveBeenCalled();
    }
  );

  it.each([undefined, '', ' ', 'a b', '../x', 'x'.repeat(129), 'tok;drop', 5, null, {}])(
    'refuses the token %j before any backend call, with the review-again message',
    async (token) => {
      const result = await dismissSourceChangeAction('o1', token as never);

      expect(result).toEqual({ success: false, error: OFFER_CHANGED_MESSAGE });
      expect(mocks.dismissAdminOfferSourceChange).not.toHaveBeenCalled();
    }
  );

  it('logs a missing or malformed token as a warning with the action only, never the token', async () => {
    await dismissSourceChangeAction('o1', 'bad token;DROP');

    expect(mocks.logWarn).toHaveBeenCalledWith(
      'Admin offer action: review token missing or malformed',
      { action: 'dismiss_source_change' }
    );
    expect(JSON.stringify(mocks.logWarn.mock.calls)).not.toContain('DROP');
  });
});
