// @vitest-environment node
/**
 * applyPartnerUpdateAction and dismissPartnerUpdateAction: the admin gate runs first, the id, the review token and
 * the proposal hash are validated before the API is called, every failure is a curated message (never backend
 * text), and a success refreshes the admin pages (applying also refreshes the public page of a live offer). The codes
 * applying can answer with have their own curated wording, so each is asserted with the code the backend sends.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

const SECRET = 'SECRET-sk_live_abc123 postgres://user:pw@host/db';

const mocks = vi.hoisted(() => ({
  requireAdminSession: vi.fn(),
  applyAdminOfferPartnerUpdate: vi.fn(),
  dismissAdminOfferPartnerUpdate: vi.fn(),
  fetchAdminOffer: vi.fn(),
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
    fetchAdminOffer: mocks.fetchAdminOffer,
  };
});

import {
  applyPartnerUpdateAction,
  dismissPartnerUpdateAction,
} from '@/app/actions/admin/offer-actions';
import { ADMIN_FORBIDDEN_MESSAGE, ADMIN_UNAVAILABLE_MESSAGE } from '@/lib/api/admin-errors';
import {
  NO_PARTNER_UPDATE_MESSAGE,
  OFFER_CHANGED_MESSAGE,
  OFFER_NOT_FOUND_MESSAGE,
  PARTNER_UPDATE_ARCHIVED_MESSAGE,
  PARTNER_UPDATE_NOT_APPLICABLE_MESSAGE,
  RATE_LIMITED_MESSAGE,
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
  mocks.fetchAdminOffer.mockResolvedValue(ok({ ...OFFER, status: 'DRAFT' }));
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

  it('answers 429 with the curated rate-limit message, never the backend text', async () => {
    call.mockResolvedValue(fail(429, undefined, SECRET));

    const result = await action('o1', TOKEN, HASH);

    expect(result).toEqual({ success: false, error: RATE_LIMITED_MESSAGE });
    expect(JSON.stringify(result)).not.toContain('SECRET');
  });

  it('answers an unreachable backend (no status) with the curated unavailable message', async () => {
    call.mockResolvedValue(fail(undefined, undefined, SECRET));

    const result = await action('o1', TOKEN, HASH);

    expect(result).toEqual({ success: false, error: ADMIN_UNAVAILABLE_MESSAGE });
    expect(JSON.stringify(result)).not.toContain('SECRET');
    expect(mocks.revalidatePath).not.toHaveBeenCalled();
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

const APPLY_ONLY_REFUSALS = [
  [
    'a live offer without offers.publish (403 permission_denied)',
    fail(403, 'permission_denied'),
    'Applying a retailer update to a published offer needs permission to publish. Ask an editor who can publish.',
  ],
  [
    'a stored proposal that fails re-validation (422 invalid_pending_payload)',
    fail(422, 'invalid_pending_payload'),
    'This retailer update cannot be applied as it was sent. Dismiss it and ask the retailer to send it again.',
  ],
  [
    "a link that is not on the merchant's website (422 destination_not_allowed)",
    fail(422, 'destination_not_allowed'),
    "The retailer's link is not on the merchant's website, so the update cannot be applied. Check the merchant's website address, or dismiss the update.",
  ],
  [
    'a category that no longer exists (422 unknown_category)',
    fail(422, 'unknown_category'),
    'The category in this retailer update no longer exists, so it cannot be applied. Dismiss it and ask the retailer to send it again.',
  ],
  [
    'a result that cannot be published (422 publish_blocked), naming only reasons from the fixed table',
    fail(
      422,
      'publish_blocked',
      `The offer cannot be published yet: its end date has passed; ${SECRET}`
    ),
    'The retailer update cannot be applied because the published offer would no longer meet the publishing rules: its end date has passed.',
  ],
  [
    'a publish_blocked answer with no known reason',
    fail(422, 'publish_blocked', SECRET),
    'The retailer update cannot be applied because the published offer would no longer meet the publishing rules.',
  ],
] as const;

describe('apply partner update action: refusals with wording of their own', () => {
  it.each(APPLY_ONLY_REFUSALS)('says what happened for %s', async (_name, refusal, message) => {
    mocks.applyAdminOfferPartnerUpdate.mockResolvedValue(refusal);

    const result = await applyPartnerUpdateAction('o1', TOKEN, HASH);

    expect(result).toEqual({ success: false, error: message });
    expect(JSON.stringify(result)).not.toContain('SECRET');
    expect(mocks.revalidatePath).not.toHaveBeenCalled();
  });

  it('never mentions the commercial basis when applying to a live offer is refused for permission', async () => {
    mocks.applyAdminOfferPartnerUpdate.mockResolvedValue(fail(403, 'permission_denied'));

    const result = await applyPartnerUpdateAction('o1', TOKEN, HASH);

    expect(result).toMatchObject({ success: false });
    expect(JSON.stringify(result).toLowerCase()).not.toContain('commercial basis');
  });

  it('says the offer is archived and the update can only be dismissed, not that nothing is waiting', async () => {
    mocks.applyAdminOfferPartnerUpdate.mockResolvedValue(fail(409, 'illegal_transition'));
    mocks.fetchAdminOffer.mockResolvedValue(ok({ ...OFFER, status: 'ARCHIVED' }));

    const result = await applyPartnerUpdateAction('o1', TOKEN, HASH);

    expect(result).toEqual({ success: false, error: PARTNER_UPDATE_ARCHIVED_MESSAGE });
    expect(PARTNER_UPDATE_ARCHIVED_MESSAGE).toMatch(/archived/);
    expect(PARTNER_UPDATE_ARCHIVED_MESSAGE).toMatch(/only dismiss/);
    expect(PARTNER_UPDATE_ARCHIVED_MESSAGE).not.toMatch(/no retailer update waiting/i);
    expect(mocks.fetchAdminOffer).toHaveBeenCalledWith('o1');
    expect(mocks.revalidatePath).toHaveBeenCalledWith('/admin/offers/o1');
  });

  it('claims neither case when the offer cannot be read to tell them apart', async () => {
    mocks.applyAdminOfferPartnerUpdate.mockResolvedValue(fail(409, 'illegal_transition'));
    mocks.fetchAdminOffer.mockResolvedValue(fail(500));

    const result = await applyPartnerUpdateAction('o1', TOKEN, HASH);

    expect(result).toEqual({ success: false, error: PARTNER_UPDATE_NOT_APPLICABLE_MESSAGE });
  });

  it('does not read the offer when a dismissal is told nothing is waiting', async () => {
    mocks.dismissAdminOfferPartnerUpdate.mockResolvedValue(fail(409, 'illegal_transition'));

    const result = await dismissPartnerUpdateAction('o1', TOKEN, HASH);

    expect(result).toEqual({ success: false, error: NO_PARTNER_UPDATE_MESSAGE });
    expect(mocks.fetchAdminOffer).not.toHaveBeenCalled();
  });

  it.each(['constructor', '__proto__', 'toString'])(
    'treats the backend code %s as an unknown code, not a table entry',
    async (code) => {
      mocks.applyAdminOfferPartnerUpdate.mockResolvedValue(fail(422, code));

      const result = await applyPartnerUpdateAction('o1', TOKEN, HASH);

      expect(result).toEqual({
        success: false,
        error: 'Some values were rejected. Check the form and try again.',
      });
    }
  );

  it('does not give a dismissal the apply wording', async () => {
    mocks.dismissAdminOfferPartnerUpdate.mockResolvedValue(fail(422, 'invalid_pending_payload'));

    const result = await dismissPartnerUpdateAction('o1', TOKEN, HASH);

    expect(JSON.stringify(result)).not.toMatch(/ask the retailer to send it again/);
  });
});

describe('what a successful apply refreshes', () => {
  it('leaves the public catalogue alone for a draft', async () => {
    mocks.applyAdminOfferPartnerUpdate.mockResolvedValue(ok({ ...OFFER, status: 'DRAFT' }));

    await applyPartnerUpdateAction('o1', TOKEN, HASH);

    expect(mocks.revalidateCatalogue).not.toHaveBeenCalled();
  });

  it('refreshes the list and the page of a published offer', async () => {
    mocks.applyAdminOfferPartnerUpdate.mockResolvedValue(ok(OFFER));

    await applyPartnerUpdateAction('o1', TOKEN, HASH);

    expect(mocks.revalidateCatalogue).toHaveBeenCalledTimes(1);
    expect(mocks.revalidateCatalogue.mock.calls[0][0]).toEqual([
      '/freebies',
      '/freebies/free-coffee',
    ]);
  });
});
