// @vitest-environment node
/**
 * The admin catalogue server actions: the admin gate runs first (a failure calls nothing), input is validated
 * with the form schema, failures become curated messages, and a successful write refreshes the admin pages and,
 * where the public site can change, the public catalogue.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

const SECRET = 'SECRET-sk_live_abc123 postgres://user:pw@host/db';

const mocks = vi.hoisted(() => ({
  requireAdminSession: vi.fn(),
  createAdminOffer: vi.fn(),
  patchAdminOffer: vi.fn(),
  runAdminOfferAction: vi.fn(),
  createAdminCategory: vi.fn(),
  patchAdminCategory: vi.fn(),
  setAdminCategoryEnabled: vi.fn(),
  createAdminMerchant: vi.fn(),
  patchAdminMerchant: vi.fn(),
  setAdminMerchantActive: vi.fn(),
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
    createAdminOffer: mocks.createAdminOffer,
    patchAdminOffer: mocks.patchAdminOffer,
    runAdminOfferAction: mocks.runAdminOfferAction,
  };
});
vi.mock('@/lib/api/admin-categories', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api/admin-categories')>(
    '@/lib/api/admin-categories'
  );
  return {
    ...actual,
    createAdminCategory: mocks.createAdminCategory,
    patchAdminCategory: mocks.patchAdminCategory,
    setAdminCategoryEnabled: mocks.setAdminCategoryEnabled,
  };
});
vi.mock('@/lib/api/admin-merchants', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api/admin-merchants')>(
    '@/lib/api/admin-merchants'
  );
  return {
    ...actual,
    createAdminMerchant: mocks.createAdminMerchant,
    patchAdminMerchant: mocks.patchAdminMerchant,
    setAdminMerchantActive: mocks.setAdminMerchantActive,
  };
});

import {
  createOfferAction,
  offerLifecycleAction,
  updateOfferAction,
} from '@/app/actions/admin/offer-actions';
import {
  createCategoryAction,
  setCategoryEnabledAction,
  updateCategoryAction,
} from '@/app/actions/admin/category-actions';
import {
  createMerchantAction,
  setMerchantActiveAction,
  updateMerchantAction,
} from '@/app/actions/admin/merchant-actions';
import { emptyOfferFormValues } from '@/lib/admin-catalogue/offer-schema';
import { emptyCategoryFormValues } from '@/lib/admin-catalogue/category-schema';
import { emptyMerchantFormValues } from '@/lib/admin-catalogue/merchant-schema';
import { ADMIN_FORBIDDEN_MESSAGE, ADMIN_UNAVAILABLE_MESSAGE } from '@/lib/api/admin-errors';
import {
  INVALID_FORM_MESSAGE,
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

const OFFER = {
  id: 'o1',
  slug: 'free-coffee',
  status: 'PUBLISHED',
  last_verified_at: '2026-01-02T00:00:00Z',
  review_token: 'f'.repeat(64),
  previous_review_token: 'e'.repeat(64),
};

const TOKEN = 'tok-1_ABC';

const offerInput = {
  ...emptyOfferFormValues(),
  commercialBasis: 'EDITORIAL',
  title: 'Free coffee',
  summary: 'A sachet.',
  categoryId: 'c1',
  merchantId: 'm1',
  imageLabel: 'coffee',
  imageAlt: 'A sachet',
  eligibility: 'UK',
  availability: 'Now',
};
const categoryInput = {
  ...emptyCategoryFormValues(),
  name: 'Beauty',
  shortName: 'Beauty',
  description: 'Skincare.',
  intro: 'Try it.',
};
const merchantInput = { ...emptyMerchantFormValues(), name: 'Acme' };

const ALL_ACTIONS: ReadonlyArray<[string, () => Promise<unknown>]> = [
  ['createOfferAction', () => createOfferAction(offerInput)],
  ['updateOfferAction', () => updateOfferAction('o1', offerInput)],
  ['offerLifecycleAction', () => offerLifecycleAction('o1', 'publish', TOKEN)],
  ['createCategoryAction', () => createCategoryAction(categoryInput)],
  ['updateCategoryAction', () => updateCategoryAction('c1', categoryInput)],
  ['setCategoryEnabledAction', () => setCategoryEnabledAction('c1', false)],
  ['createMerchantAction', () => createMerchantAction(merchantInput)],
  ['updateMerchantAction', () => updateMerchantAction('m1', merchantInput)],
  ['setMerchantActiveAction', () => setMerchantActiveAction('m1', false)],
];

function noBackendCall(): void {
  for (const name of [
    'createAdminOffer',
    'patchAdminOffer',
    'runAdminOfferAction',
    'createAdminCategory',
    'patchAdminCategory',
    'setAdminCategoryEnabled',
    'createAdminMerchant',
    'patchAdminMerchant',
    'setAdminMerchantActive',
    'revalidatePath',
    'revalidateCatalogue',
  ] as const) {
    expect(mocks[name], name).not.toHaveBeenCalled();
  }
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.requireAdminSession.mockResolvedValue({ ok: true });
});

describe('authorisation', () => {
  it.each(ALL_ACTIONS)('%s refuses a non-admin before calling anything', async (_name, run) => {
    mocks.requireAdminSession.mockResolvedValue({ ok: false, error: ADMIN_FORBIDDEN_MESSAGE });

    await expect(run()).resolves.toEqual({ success: false, error: ADMIN_FORBIDDEN_MESSAGE });

    noBackendCall();
  });

  it.each(ALL_ACTIONS)(
    '%s reports an unavailable admin check without writing',
    async (_name, run) => {
      mocks.requireAdminSession.mockResolvedValue({ ok: false, error: ADMIN_UNAVAILABLE_MESSAGE });

      await expect(run()).resolves.toEqual({ success: false, error: ADMIN_UNAVAILABLE_MESSAGE });

      noBackendCall();
    }
  );

  it.each(ALL_ACTIONS)('%s turns a backend 403 into the curated refusal', async (name, run) => {
    for (const key of [
      'createAdminOffer',
      'patchAdminOffer',
      'runAdminOfferAction',
      'createAdminCategory',
      'patchAdminCategory',
      'setAdminCategoryEnabled',
      'createAdminMerchant',
      'patchAdminMerchant',
      'setAdminMerchantActive',
    ] as const) {
      mocks[key].mockResolvedValue(fail(403));
    }

    const result = await run();

    expect(result, name).toEqual({ success: false, error: ADMIN_FORBIDDEN_MESSAGE });
    expect(mocks.revalidateCatalogue).not.toHaveBeenCalled();
    expect(mocks.logError).not.toHaveBeenCalled();
  });
});

describe('offer actions', () => {
  it('creates a draft, refreshes only the admin list and returns the new ids', async () => {
    mocks.createAdminOffer.mockResolvedValue(ok({ ...OFFER, status: 'DRAFT' }));

    const result = await createOfferAction(offerInput);

    expect(result).toEqual({
      success: true,
      data: { id: 'o1', slug: 'free-coffee', status: 'DRAFT' },
    });
    expect(mocks.createAdminOffer.mock.calls[0][0]).toMatchObject({
      title: 'Free coffee',
      category_id: 'c1',
    });
    expect(mocks.revalidatePath).toHaveBeenCalledWith('/admin/offers');
    expect(mocks.revalidateCatalogue).not.toHaveBeenCalled();
  });

  it('returns per-field messages for invalid input and calls nothing', async () => {
    const result = await createOfferAction({
      ...offerInput,
      title: '',
      sourceUrl: 'http://x.test',
    });

    expect(result).toMatchObject({
      success: false,
      error: INVALID_FORM_MESSAGE,
      fieldErrors: { title: 'Enter a title.', sourceUrl: 'Enter a full https:// address.' },
    });
    expect(mocks.createAdminOffer).not.toHaveBeenCalled();
  });

  it('rejects a non-object input without throwing', async () => {
    await expect(createOfferAction(null)).resolves.toMatchObject({ success: false });
    await expect(updateOfferAction('o1', 'nope')).resolves.toMatchObject({ success: false });
    expect(mocks.createAdminOffer).not.toHaveBeenCalled();
    expect(mocks.patchAdminOffer).not.toHaveBeenCalled();
  });

  it('maps a taken slug to its curated message and never echoes backend text', async () => {
    mocks.createAdminOffer.mockResolvedValue(fail(409, 'slug_taken'));

    const result = await createOfferAction(offerInput);

    expect(result).toEqual({
      success: false,
      error: 'That slug is already in use. Choose another.',
      fieldErrors: { slug: 'That slug is already in use. Choose another.' },
    });
    expect(JSON.stringify(result)).not.toContain('SECRET');
  });

  it('updates, refreshes the admin pages, and the public page when the offer is published', async () => {
    mocks.patchAdminOffer.mockResolvedValue(ok(OFFER));

    const result = await updateOfferAction('o1', offerInput);

    expect(result).toMatchObject({
      success: true,
      data: {
        id: 'o1',
        status: 'PUBLISHED',
        reviewToken: 'f'.repeat(64),
        previousReviewToken: 'e'.repeat(64),
      },
    });
    expect(mocks.patchAdminOffer.mock.calls[0][0]).toBe('o1');
    expect('slug' in mocks.patchAdminOffer.mock.calls[0][1]).toBe(false);
    expect(mocks.revalidatePath).toHaveBeenCalledWith('/admin/offers');
    expect(mocks.revalidatePath).toHaveBeenCalledWith('/admin/offers/o1');
    expect(mocks.revalidateCatalogue).toHaveBeenCalledWith(['/freebies', '/freebies/free-coffee']);
  });

  it('saves an offer whose basis nobody has chosen yet, leaving the basis out of the update', async () => {
    mocks.patchAdminOffer.mockResolvedValue(ok({ ...OFFER, status: 'DRAFT' }));

    const result = await updateOfferAction('o1', { ...offerInput, commercialBasis: '' }, TOKEN);

    expect(result).toMatchObject({ success: true });
    const body = mocks.patchAdminOffer.mock.calls[0][1];
    expect('commercial_basis' in body).toBe(false);
    expect(body).toMatchObject({ title: 'Free coffee', review_token: TOKEN });
  });

  it('sends the basis an editor did choose', async () => {
    mocks.patchAdminOffer.mockResolvedValue(ok({ ...OFFER, status: 'DRAFT' }));

    await updateOfferAction('o1', { ...offerInput, commercialBasis: 'AFFILIATE' }, TOKEN);

    expect(mocks.patchAdminOffer.mock.calls[0][1]).toMatchObject({
      commercial_basis: 'AFFILIATE',
    });
  });

  it("answers a retailer's submission moved to another merchant (422 partner_merchant_fixed) with one curated message on the merchant field", async () => {
    mocks.patchAdminOffer.mockResolvedValue(fail(422, 'partner_merchant_fixed'));

    const result = await updateOfferAction('o1', offerInput);

    expect(result).toEqual({
      success: false,
      error: "A retailer's submission cannot be moved to another merchant.",
      fieldErrors: { merchantId: "A retailer's submission cannot be moved to another merchant." },
    });
    expect(JSON.stringify(result)).not.toContain('SECRET');
  });

  it('does not touch the public catalogue when a draft is edited', async () => {
    mocks.patchAdminOffer.mockResolvedValue(ok({ ...OFFER, status: 'DRAFT' }));
    await updateOfferAction('o1', offerInput);
    expect(mocks.revalidateCatalogue).not.toHaveBeenCalled();
  });

  it.each(['../x', 'a/b', '', 'x'.repeat(129), 'o 1'])('refuses the offer id %j', async (id) => {
    await expect(updateOfferAction(id, offerInput)).resolves.toEqual({
      success: false,
      error: OFFER_NOT_FOUND_MESSAGE,
    });
    await expect(offerLifecycleAction(id, 'publish')).resolves.toEqual({
      success: false,
      error: OFFER_NOT_FOUND_MESSAGE,
    });
    expect(mocks.patchAdminOffer).not.toHaveBeenCalled();
    expect(mocks.runAdminOfferAction).not.toHaveBeenCalled();
  });

  it.each(['publish', 'unpublish', 'expire', 'archive', 'verify'] as const)(
    'runs %s and refreshes the admin and public pages',
    async (action) => {
      mocks.runAdminOfferAction.mockResolvedValue(ok(OFFER));

      const result = await offerLifecycleAction('o1', action, TOKEN);

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
      expect(mocks.runAdminOfferAction).toHaveBeenCalledWith(
        'o1',
        action === 'publish' || action === 'verify' ? { action, reviewToken: TOKEN } : { action }
      );
      expect(mocks.revalidatePath).toHaveBeenCalledWith('/admin/offers');
      expect(mocks.revalidateCatalogue).toHaveBeenCalledWith([
        '/freebies',
        '/freebies/free-coffee',
      ]);
    }
  );

  it('sends the loaded review token with an edit so a stale basis change is refused', async () => {
    mocks.patchAdminOffer.mockResolvedValue(ok(OFFER));

    await updateOfferAction('o1', offerInput, TOKEN);
    await updateOfferAction('o1', offerInput);
    await updateOfferAction('o1', offerInput, '../not a token');

    expect(mocks.patchAdminOffer.mock.calls.map((call) => call[1].review_token)).toEqual([
      TOKEN,
      undefined,
      undefined,
    ]);
  });

  it('turns a 409 offer_changed on an edit into the curated message and refreshes the page', async () => {
    mocks.patchAdminOffer.mockResolvedValue(fail(409, 'offer_changed'));

    const result = await updateOfferAction('o1', offerInput, TOKEN);

    expect(result).toEqual({
      success: false,
      error: OFFER_CHANGED_MESSAGE,
      reason: 'offer_changed',
    });
    expect(JSON.stringify(result)).not.toContain('SECRET');
    expect(mocks.revalidatePath).toHaveBeenCalledWith('/admin/offers/o1');
    expect(mocks.revalidateCatalogue).not.toHaveBeenCalled();
  });

  it('explains that changing the basis of a published offer needs the publish permission', async () => {
    mocks.patchAdminOffer.mockResolvedValue(fail(403, 'permission_denied'));

    const result = await updateOfferAction('o1', offerInput, TOKEN);

    expect(result).toEqual({
      success: false,
      error:
        'Changing the commercial basis of a published offer needs permission to publish. Ask an editor who can publish.',
    });
  });

  it('sends the confirmed commercial basis with publish only, and only a known one', async () => {
    mocks.runAdminOfferAction.mockResolvedValue(ok(OFFER));

    await offerLifecycleAction('o1', 'publish', TOKEN, 'SPONSORED');
    await offerLifecycleAction('o1', 'publish', TOKEN, 'PAID');
    await offerLifecycleAction('o1', 'verify', TOKEN, 'SPONSORED');
    await offerLifecycleAction('o1', 'unpublish', undefined, 'SPONSORED');

    expect(mocks.runAdminOfferAction.mock.calls).toEqual([
      ['o1', { action: 'publish', reviewToken: TOKEN, commercialBasis: 'SPONSORED' }],
      ['o1', { action: 'publish', reviewToken: TOKEN }],
      ['o1', { action: 'verify', reviewToken: TOKEN }],
      ['o1', { action: 'unpublish' }],
    ]);
  });

  it('turns an unconfirmed basis into the curated message, never the backend text', async () => {
    mocks.runAdminOfferAction.mockResolvedValue(
      fail(
        422,
        'publish_blocked',
        'The offer cannot be published yet: its commercial basis has not been confirmed'
      )
    );

    const result = await offerLifecycleAction('o1', 'publish', TOKEN);

    expect(result).toEqual({
      success: false,
      error:
        'This offer cannot be published yet: its commercial basis has not been confirmed (confirm it on the offer page; for a live offer, unpublish it, change its links, then publish again).',
    });
  });

  it('refuses a create without a commercial basis, naming the field, and calls nothing', async () => {
    const result = await createOfferAction({ ...offerInput, commercialBasis: '' });

    expect(result).toMatchObject({
      success: false,
      fieldErrors: { commercialBasis: 'Choose the commercial basis.' },
    });
    expect(mocks.createAdminOffer).not.toHaveBeenCalled();
  });

  it('rejects an unknown lifecycle action without calling the API', async () => {
    const result = await offerLifecycleAction('o1', 'delete' as never);
    expect(result.success).toBe(false);
    expect(mocks.runAdminOfferAction).not.toHaveBeenCalled();
  });

  it('does not hand a malformed id from the API back to the browser router', async () => {
    mocks.createAdminOffer.mockResolvedValue(ok({ ...OFFER, id: '../../admin' }));

    const result = await createOfferAction(offerInput);

    expect(result).toEqual({ success: false, error: 'Something went wrong. Please try again.' });
    expect(mocks.revalidatePath).not.toHaveBeenCalled();
  });

  it('lists the unmet publish rules and refreshes nothing when publish is blocked', async () => {
    mocks.runAdminOfferAction.mockResolvedValue(
      fail(422, 'publish_blocked', 'The offer cannot be published yet: it has not been verified')
    );

    const result = await offerLifecycleAction('o1', 'publish', TOKEN);

    expect(result).toEqual({
      success: false,
      error: 'This offer cannot be published yet: it has not been verified.',
    });
    expect(mocks.revalidatePath).not.toHaveBeenCalled();
    expect(mocks.revalidateCatalogue).not.toHaveBeenCalled();
  });

  it('explains an illegal transition and logs unexpected failures without backend text', async () => {
    mocks.runAdminOfferAction.mockResolvedValueOnce(fail(409, 'illegal_transition'));
    const refused = await offerLifecycleAction('o1', 'unpublish');
    expect(refused).toMatchObject({ success: false });
    expect(JSON.stringify(refused)).not.toContain('SECRET');

    mocks.runAdminOfferAction.mockResolvedValueOnce(fail(500));
    await offerLifecycleAction('o1', 'publish', TOKEN);
    expect(JSON.stringify(mocks.logError.mock.calls)).not.toContain('SECRET');
    expect(mocks.logError).toHaveBeenCalledTimes(1);
  });

  it('reports a missing offer with the curated not-found message', async () => {
    mocks.runAdminOfferAction.mockResolvedValue(fail(404, 'not_found'));
    await expect(offerLifecycleAction('o1', 'verify', TOKEN)).resolves.toEqual({
      success: false,
      error: OFFER_NOT_FOUND_MESSAGE,
    });
  });

  it('still succeeds when the public refresh throws, because the write already happened', async () => {
    mocks.runAdminOfferAction.mockResolvedValue(ok(OFFER));
    mocks.revalidateCatalogue.mockImplementation(() => {
      throw new Error(SECRET);
    });

    const result = await offerLifecycleAction('o1', 'publish', TOKEN);

    expect(result.success).toBe(true);
    expect(JSON.stringify(mocks.logError.mock.calls)).not.toContain('SECRET');
  });

  it('skips the offer page refresh for a slug that is not a plain path segment', async () => {
    mocks.runAdminOfferAction.mockResolvedValue(ok({ ...OFFER, slug: 'Bad Slug/../' }));
    await offerLifecycleAction('o1', 'publish', TOKEN);
    expect(mocks.revalidateCatalogue).toHaveBeenCalledWith(['/freebies']);
  });
});

describe('the tokens a write returns', () => {
  it('hands back the token of the state a save replaced, for the page to compare', async () => {
    mocks.patchAdminOffer.mockResolvedValue(ok(OFFER));

    const result = await updateOfferAction('o1', offerInput);

    expect(result).toMatchObject({
      success: true,
      data: { reviewToken: 'f'.repeat(64), previousReviewToken: 'e'.repeat(64) },
    });
  });

  it.each(['has space', 'x;y', '', 'z'.repeat(129), 5, null])(
    'drops a token from the API that is not made of plain characters (%j)',
    async (bad) => {
      mocks.runAdminOfferAction.mockResolvedValue(
        ok({ ...OFFER, review_token: bad, previous_review_token: bad })
      );
      mocks.patchAdminOffer.mockResolvedValue(
        ok({ ...OFFER, review_token: bad, previous_review_token: bad })
      );

      const step = await offerLifecycleAction('o1', 'expire');
      const save = await updateOfferAction('o1', offerInput);

      for (const result of [step, save]) {
        expect(result).toMatchObject({ success: true });
        if (result.success) {
          expect(result.data.reviewToken).toBeUndefined();
          expect(result.data.previousReviewToken).toBeUndefined();
        }
      }
    }
  );
});

describe('the review token on publish and verify', () => {
  it.each(['publish', 'verify'] as const)(
    '%s without a token sends nothing and says to review the offer again',
    async (action) => {
      const result = await offerLifecycleAction('o1', action);

      expect(result).toEqual({ success: false, error: OFFER_CHANGED_MESSAGE });
      expect(mocks.runAdminOfferAction).not.toHaveBeenCalled();
      expect(mocks.revalidateCatalogue).not.toHaveBeenCalled();
    }
  );

  it.each(['', ' ', 'a b', '../x', 'x'.repeat(129), 'tok;drop', 5, null, {}])(
    'refuses the token %j before any backend call',
    async (token) => {
      const result = await offerLifecycleAction('o1', 'publish', token as never);

      expect(result).toEqual({ success: false, error: OFFER_CHANGED_MESSAGE });
      expect(mocks.runAdminOfferAction).not.toHaveBeenCalled();
    }
  );

  it.each(['unpublish', 'expire', 'archive'] as const)(
    '%s needs no token and sends none',
    async (action) => {
      mocks.runAdminOfferAction.mockResolvedValue(ok(OFFER));

      const result = await offerLifecycleAction('o1', action);

      expect(result.success).toBe(true);
      expect(mocks.runAdminOfferAction).toHaveBeenCalledWith('o1', { action });
    }
  );

  it.each(['publish', 'verify'] as const)(
    'on 409 offer_changed %s shows the curated message, refreshes the admin pages and never the public site',
    async (action) => {
      mocks.runAdminOfferAction.mockResolvedValue(fail(409, 'offer_changed'));

      const result = await offerLifecycleAction('o1', action, TOKEN);

      expect(result).toEqual({
        success: false,
        error: 'This offer changed since you opened it. Review it again.',
        reason: 'offer_changed',
      });
      expect(JSON.stringify(result)).not.toContain('SECRET');
      expect(mocks.revalidatePath).toHaveBeenCalledWith('/admin/offers');
      expect(mocks.revalidatePath).toHaveBeenCalledWith('/admin/offers/o1');
      expect(mocks.revalidateCatalogue).not.toHaveBeenCalled();
    }
  );

  it('does not refresh the admin pages for a refusal that is not offer_changed', async () => {
    mocks.runAdminOfferAction.mockResolvedValue(fail(409, 'illegal_transition'));

    await offerLifecycleAction('o1', 'publish', TOKEN);

    expect(mocks.revalidatePath).not.toHaveBeenCalled();
  });

  it('answers a 422 from the backend with a curated message and no backend text', async () => {
    mocks.runAdminOfferAction.mockResolvedValue(fail(422));

    const result = await offerLifecycleAction('o1', 'publish', TOKEN);

    expect(result).toEqual({
      success: false,
      error: 'Some values were rejected. Check the form and try again.',
    });
  });

  it('maps a review_token_required answer to the same review-again message, without a reason', async () => {
    mocks.runAdminOfferAction.mockResolvedValue(fail(422, 'review_token_required'));

    const result = await offerLifecycleAction('o1', 'verify', TOKEN);

    expect(result).toEqual({
      success: false,
      error: 'This offer changed since you opened it. Review it again.',
    });
  });

  it('logs a missing or malformed token as a warning with the action only, never the token', async () => {
    await offerLifecycleAction('o1', 'publish', 'bad token;DROP');
    await offerLifecycleAction('o1', 'verify');

    expect(mocks.logWarn).toHaveBeenCalledTimes(2);
    expect(mocks.logWarn).toHaveBeenNthCalledWith(
      1,
      'Admin offer action: review token missing or malformed',
      { action: 'publish' }
    );
    expect(JSON.stringify(mocks.logWarn.mock.calls)).not.toContain('DROP');
  });

  it('accepts a realistic 64-character hex token', async () => {
    mocks.runAdminOfferAction.mockResolvedValue(ok(OFFER));
    const token = '0123456789abcdef'.repeat(4);

    const result = await offerLifecycleAction('o1', 'publish', token);

    expect(result.success).toBe(true);
    expect(mocks.runAdminOfferAction).toHaveBeenCalledWith('o1', {
      action: 'publish',
      reviewToken: token,
    });
  });

  it.each(['unpublish', 'expire', 'archive'] as const)(
    'ignores a garbage token passed with %s and sends the call without one',
    async (action) => {
      mocks.runAdminOfferAction.mockResolvedValue(ok(OFFER));

      const result = await offerLifecycleAction('o1', action, 'garbage token;;' as string);

      expect(result.success).toBe(true);
      expect(mocks.runAdminOfferAction).toHaveBeenCalledWith('o1', { action });
      expect(mocks.logWarn).not.toHaveBeenCalled();
    }
  );
});

describe('category actions', () => {
  const CATEGORY = { id: 'c1', slug: 'beauty', enabled: true };

  it('creates and refreshes admin and public pages', async () => {
    mocks.createAdminCategory.mockResolvedValue(ok(CATEGORY));
    await expect(createCategoryAction(categoryInput)).resolves.toEqual({
      success: true,
      data: CATEGORY,
    });
    expect(mocks.revalidatePath).toHaveBeenCalledWith('/admin/categories');
    expect(mocks.revalidateCatalogue).toHaveBeenCalledWith(['/freebies']);
  });

  it('validates input, ids and the sort order', async () => {
    const bad = await createCategoryAction({ ...categoryInput, name: '', sortOrder: '20000' });
    expect(bad).toMatchObject({ success: false, fieldErrors: { name: 'Enter a name.' } });
    expect((bad as { fieldErrors: Record<string, string> }).fieldErrors.sortOrder).toBeDefined();
    await expect(updateCategoryAction('../x', categoryInput)).resolves.toMatchObject({
      success: false,
    });
    expect(mocks.createAdminCategory).not.toHaveBeenCalled();
    expect(mocks.patchAdminCategory).not.toHaveBeenCalled();
  });

  it('updates, and toggles enabled with a strict boolean', async () => {
    mocks.patchAdminCategory.mockResolvedValue(ok(CATEGORY));
    mocks.setAdminCategoryEnabled.mockResolvedValue(ok({ ...CATEGORY, enabled: false }));

    await updateCategoryAction('c1', categoryInput);
    const toggled = await setCategoryEnabledAction('c1', false);
    await setCategoryEnabledAction('c1', 'yes' as never);

    expect(toggled).toEqual({ success: true, data: { ...CATEGORY, enabled: false } });
    expect(mocks.setAdminCategoryEnabled).toHaveBeenNthCalledWith(1, 'c1', false);
    expect(mocks.setAdminCategoryEnabled).toHaveBeenNthCalledWith(2, 'c1', false);
    expect(mocks.revalidatePath).toHaveBeenCalledWith('/admin/categories/c1');
  });

  it('maps a taken slug and a missing category to curated messages', async () => {
    mocks.createAdminCategory.mockResolvedValue(fail(409, 'slug_taken'));
    mocks.patchAdminCategory.mockResolvedValue(fail(404, 'not_found'));
    await expect(createCategoryAction(categoryInput)).resolves.toMatchObject({
      error: 'That slug is already in use. Choose another.',
    });
    await expect(updateCategoryAction('c1', categoryInput)).resolves.toMatchObject({
      error: 'That category no longer exists. Go back to the list.',
    });
  });
});

describe('merchant actions', () => {
  const MERCHANT = { id: 'm1', slug: 'acme', active: true };

  it('creates, updates and toggles, refreshing admin and public pages', async () => {
    mocks.createAdminMerchant.mockResolvedValue(ok(MERCHANT));
    mocks.patchAdminMerchant.mockResolvedValue(ok(MERCHANT));
    mocks.setAdminMerchantActive.mockResolvedValue(ok({ ...MERCHANT, active: false }));

    await expect(createMerchantAction(merchantInput)).resolves.toEqual({
      success: true,
      data: MERCHANT,
    });
    await updateMerchantAction('m1', merchantInput);
    await expect(setMerchantActiveAction('m1', false)).resolves.toEqual({
      success: true,
      data: { ...MERCHANT, active: false },
    });
    expect(mocks.revalidatePath).toHaveBeenCalledWith('/admin/merchants');
    expect(mocks.revalidatePath).toHaveBeenCalledWith('/admin/merchants/m1');
    expect(mocks.revalidateCatalogue).toHaveBeenCalledWith(['/freebies']);
  });

  it('rejects a non-https website and a bad id before calling the API', async () => {
    const bad = await createMerchantAction({ ...merchantInput, website: 'http://acme.test' });
    expect(bad).toMatchObject({
      success: false,
      fieldErrors: { website: 'Enter a full https:// address.' },
    });
    await expect(setMerchantActiveAction('a/b', true)).resolves.toMatchObject({ success: false });
    expect(mocks.createAdminMerchant).not.toHaveBeenCalled();
    expect(mocks.setAdminMerchantActive).not.toHaveBeenCalled();
  });

  it('maps a rejected URL to its curated message', async () => {
    mocks.createAdminMerchant.mockResolvedValue(fail(422, 'invalid_url'));
    await expect(createMerchantAction(merchantInput)).resolves.toEqual({
      success: false,
      error: 'A web address was rejected. Use a full https:// address.',
    });
  });
});
