// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const logger = vi.hoisted(() => ({
  info: vi.fn(),
  warn: vi.fn(),
  error: vi.fn(),
  debug: vi.fn(),
}));
vi.mock('@/lib/logger', () => ({ logger }));

import { loadSavedOffersAction } from '@/app/actions/saved-actions';
import { CatalogueUnavailableError } from '@/lib/catalogue/errors';
import { fixtureSource } from '@/lib/catalogue/fixture-source';
import { FIXTURE_TODAY, MERCHANTS, OFFERS } from '@/lib/catalogue/fixtures';
import { SAVED_OFFERS_REQUEST_LIMIT } from '@/lib/saved/limits';

const live = OFFERS.filter(
  (offer) => offer.status === 'published' && (!offer.endAt || offer.endAt >= FIXTURE_TODAY)
);
const draft = OFFERS.find((offer) => offer.status === 'draft');
const expired = OFFERS.find((offer) => offer.status === 'expired');

function dataOf(result: Awaited<ReturnType<typeof loadSavedOffersAction>>) {
  if (!result.success) throw new Error(`expected success, got: ${result.error}`);
  return result.data;
}

afterEach(() => {
  vi.restoreAllMocks();
  Object.values(logger).forEach((fn) => fn.mockReset());
});

describe('loadSavedOffersAction: validation', () => {
  beforeEach(() => {
    vi.spyOn(fixtureSource, 'lookupOffer');
  });

  it.each([
    ['not an array', 'glow'],
    ['null', null],
    ['an object', { slugs: ['a'] }],
    ['an empty list', []],
    ['a non-string entry', ['ok', 42]],
    ['an uppercase slug', ['Glow-Cream']],
    ['a path-like slug', ['../etc/passwd']],
    ['a slug with a query string', ['a?b=c']],
    ['an over-long slug', ['a'.repeat(101)]],
    ['an empty slug', ['']],
  ])('refuses %s without reading the catalogue', async (_name, input) => {
    const result = await loadSavedOffersAction(input);

    expect(result.success).toBe(false);
    expect(fixtureSource.lookupOffer).not.toHaveBeenCalled();
  });

  it('refuses more slugs than the request limit and accepts exactly the limit', async () => {
    const slugs = (count: number) => Array.from({ length: count }, (_, index) => `offer-${index}`);

    const tooMany = await loadSavedOffersAction(slugs(SAVED_OFFERS_REQUEST_LIMIT + 1));
    expect(tooMany.success).toBe(false);
    expect(fixtureSource.lookupOffer).not.toHaveBeenCalled();

    const atLimit = await loadSavedOffersAction(slugs(SAVED_OFFERS_REQUEST_LIMIT));
    expect(atLimit.success).toBe(true);
    expect(fixtureSource.lookupOffer).toHaveBeenCalledTimes(SAVED_OFFERS_REQUEST_LIMIT);
  });

  it('gives a curated message and nothing from the input back', async () => {
    const result = await loadSavedOffersAction(['<script>alert(1)</script>']);

    expect(result).toEqual({ success: false, error: expect.any(String) });
    expect(JSON.stringify(result)).not.toContain('script');
  });
});

describe('loadSavedOffersAction: only public published offers come back', () => {
  it('returns live published offers in the order asked, once each, with their merchant', async () => {
    const [first, second] = live;
    const result = dataOf(await loadSavedOffersAction([second.slug, first.slug, second.slug]));

    expect(result.offers.map((offer) => offer.slug)).toEqual([second.slug, first.slug]);
    expect(result.missing).toEqual([]);
    expect(result.unavailable).toEqual([]);
    const merchant = MERCHANTS.find((item) => item.slug === second.merchantSlug);
    expect(result.offers[0].merchant).toEqual(merchant);
  });

  it('reports a slug nobody published as missing', async () => {
    const result = dataOf(await loadSavedOffersAction(['no-such-offer', live[0].slug]));

    expect(result.offers.map((offer) => offer.slug)).toEqual([live[0].slug]);
    expect(result.missing).toEqual(['no-such-offer']);
  });

  it('never returns a draft offer', async () => {
    expect(draft).toBeDefined();
    const result = dataOf(await loadSavedOffersAction([draft!.slug]));

    expect(result.offers).toEqual([]);
    expect(result.missing).toEqual([draft!.slug]);
  });

  it('never returns an expired offer, which the catalogue still looks up as found', async () => {
    expect(expired).toBeDefined();
    const result = dataOf(await loadSavedOffersAction([expired!.slug]));

    expect(result.offers).toEqual([]);
    expect(result.missing).toEqual([expired!.slug]);
  });

  it('treats an offer past its end date as missing even while its status still says published', async () => {
    const pastEnd = { ...live[0], status: 'published' as const, endAt: '2026-10-01' };
    vi.spyOn(fixtureSource, 'lookupOffer').mockResolvedValue({ kind: 'found', offer: pastEnd });

    const result = dataOf(await loadSavedOffersAction([pastEnd.slug]));

    expect(result.offers).toEqual([]);
    expect(result.missing).toEqual([pastEnd.slug]);
  });

  it('treats an ended offer (the API 410) as missing', async () => {
    vi.spyOn(fixtureSource, 'lookupOffer').mockResolvedValue({
      kind: 'ended',
      ended: {
        slug: 'gone-offer',
        title: 'Gone',
        categorySlug: 'beauty',
        categoryName: 'Beauty',
        endedAt: '2026-10-01',
      },
    });

    const result = dataOf(await loadSavedOffersAction(['gone-offer']));

    expect(result.offers).toEqual([]);
    expect(result.missing).toEqual(['gone-offer']);
  });
});

describe('loadSavedOffersAction: outages never prune', () => {
  it('reports a slug it could not read as unavailable, not missing, and still returns the rest', async () => {
    const [ok, broken] = live;
    const real = fixtureSource.lookupOffer.bind(fixtureSource);
    vi.spyOn(fixtureSource, 'lookupOffer').mockImplementation(async (slug) => {
      if (slug === broken.slug) throw new CatalogueUnavailableError();
      return real(slug);
    });

    const result = dataOf(await loadSavedOffersAction([ok.slug, broken.slug]));

    expect(result.offers.map((offer) => offer.slug)).toEqual([ok.slug]);
    expect(result.unavailable).toEqual([broken.slug]);
    expect(result.missing).toEqual([]);
  });

  it('treats an unexpected error as unavailable and logs only its name', async () => {
    vi.spyOn(fixtureSource, 'lookupOffer').mockRejectedValue(new TypeError('secret detail'));

    const result = dataOf(await loadSavedOffersAction([live[0].slug]));

    expect(result.unavailable).toEqual([live[0].slug]);
    expect(result.missing).toEqual([]);
    const logged = JSON.stringify(logger.error.mock.calls);
    expect(logged).toContain('TypeError');
    expect(logged).not.toContain('secret detail');
    expect(logged).not.toContain(live[0].slug);
  });

  it('returns a curated failure when the date cannot be read', async () => {
    vi.spyOn(fixtureSource, 'getToday').mockRejectedValue(new CatalogueUnavailableError());

    const result = await loadSavedOffersAction([live[0].slug]);

    expect(result).toEqual({ success: false, error: expect.any(String) });
  });
});

describe('loadSavedOffersAction: load on the backend', () => {
  it('reads at most a handful of offers at the same time', async () => {
    let active = 0;
    let peak = 0;
    vi.spyOn(fixtureSource, 'lookupOffer').mockImplementation(async () => {
      active += 1;
      peak = Math.max(peak, active);
      await new Promise((resolve) => setTimeout(resolve, 1));
      active -= 1;
      return { kind: 'not_found' };
    });
    const slugs = Array.from(
      { length: SAVED_OFFERS_REQUEST_LIMIT },
      (_, index) => `offer-${index}`
    );

    await loadSavedOffersAction(slugs);

    expect(peak).toBeGreaterThan(1);
    expect(peak).toBeLessThanOrEqual(6);
  });
});
