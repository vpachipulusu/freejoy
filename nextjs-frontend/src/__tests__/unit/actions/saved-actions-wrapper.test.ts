// @vitest-environment node
/**
 * The Saved action over the REAL catalogue wrapper (only the generated client is mocked): which backend answers
 * may prune a saved offer. Pruning is irreversible, so only the backend's own "no such offer" and "ended" answers
 * do; the same status code from anything else on the path (a proxy, a wrong route) is an outage.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ readOffer: vi.fn(), warn: vi.fn() }));

vi.mock('@/lib/api/generated', () => ({
  listOffers: vi.fn(),
  readOffer: mocks.readOffer,
  listCategories: vi.fn(),
}));
vi.mock('@/lib/logger', () => ({
  logger: { warn: mocks.warn, error: vi.fn(), info: vi.fn() },
}));
vi.mock('next/headers', () => ({
  cookies: vi.fn(async () => ({ get: vi.fn() })),
  headers: vi.fn(async () => ({ get: vi.fn() })),
}));
vi.mock('next/cache', () => ({
  unstable_cache:
    (fn: (...args: unknown[]) => unknown) =>
    (...args: unknown[]) =>
      fn(...args),
}));

import { loadSavedOffersAction } from '@/app/actions/saved-actions';
import { resetRateLimitStore } from '@/lib/middleware/rate-limit';

const failed = (status: number, body?: unknown) => ({
  ...(body === undefined ? {} : { error: body }),
  response: { ok: false, status },
});

const STUB = {
  slug: 'old-tea',
  title: 'Old tea',
  category: { slug: 'food-drink', name: 'Food & drink' },
  ended_at: '2026-05-31T23:59:00Z',
};

async function resultFor(slug: string) {
  const result = await loadSavedOffersAction([slug]);
  if (!result.success) throw new Error(`expected success, got: ${result.error}`);
  return result.data;
}

beforeEach(() => {
  vi.stubEnv('CATALOGUE_SOURCE', 'api');
  vi.stubEnv('BACKEND_API_URL', 'http://backend.test');
  vi.stubEnv('BACKEND_INTERNAL_KEY', 'server-key');
  mocks.readOffer.mockReset();
  mocks.warn.mockReset();
  resetRateLimitStore();
});

describe('a 404 from the catalogue', () => {
  it('prunes only when it carries the backend offer_not_found code', async () => {
    mocks.readOffer.mockResolvedValue(
      failed(404, { success: false, error: 'Offer not found', code: 'offer_not_found' })
    );

    expect(await resultFor('gone-offer')).toEqual({
      offers: [],
      missing: ['gone-offer'],
      unavailable: [],
    });
  });

  it.each([
    ['the backend route-level "Not Found" (no code)', { success: false, error: 'Not Found' }],
    ['the offer message without the code', { success: false, error: 'Offer not found' }],
    ['another code', { success: false, error: 'Offer not found', code: 'something_else' }],
    ['an HTML page from a proxy', '<html>Not Found</html>'],
    ['no body', undefined],
  ])('keeps the offer saved for %s', async (_name, body) => {
    mocks.readOffer.mockResolvedValue(failed(404, body));

    expect(await resultFor('some-offer')).toEqual({
      offers: [],
      missing: [],
      unavailable: ['some-offer'],
    });
  });
});

describe('a 410 from the catalogue', () => {
  it('prunes when it is the backend ended envelope', async () => {
    mocks.readOffer.mockResolvedValue(
      failed(410, { success: false, error: 'This offer has ended', expired: true, data: STUB })
    );

    expect(await resultFor('old-tea')).toEqual({
      offers: [],
      missing: ['old-tea'],
      unavailable: [],
    });
  });

  it.each([
    ['an HTML page', '<html>Gone</html>'],
    ['an empty body', ''],
    ['no body', undefined],
    ['an empty object', {}],
    ['an envelope that does not say expired', { success: false, error: 'Gone', data: STUB }],
    ['an expired flag without the envelope', { expired: true, data: STUB }],
  ])('keeps the offer saved for %s', async (_name, body) => {
    mocks.readOffer.mockResolvedValue(failed(410, body));

    expect(await resultFor('old-tea')).toEqual({
      offers: [],
      missing: [],
      unavailable: ['old-tea'],
    });
  });
});
