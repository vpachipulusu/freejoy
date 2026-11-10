// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const logger = vi.hoisted(() => ({
  info: vi.fn(),
  warn: vi.fn(),
  error: vi.fn(),
  debug: vi.fn(),
}));
vi.mock('@/lib/logger', () => ({ logger }));
const ip = vi.hoisted(() => ({ read: vi.fn() }));
vi.mock('@/lib/api/server', () => ({ readTrustedClientIp: ip.read }));

import { loadSavedOffersAction } from '@/app/actions/saved-actions';
import { resetRateLimitStore } from '@/lib/middleware/rate-limit';
import { CatalogueRateLimitedError, CatalogueUnavailableError } from '@/lib/catalogue/errors';
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

beforeEach(() => {
  resetRateLimitStore();
  ip.read.mockReset();
  ip.read.mockResolvedValue(undefined);
});

afterEach(() => {
  vi.useRealTimers();
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
    expect(peak).toBeLessThanOrEqual(4);
  });
});

describe('loadSavedOffersAction: it cannot be used to flood the backend', () => {
  const manySlugs = (count: number) =>
    Array.from({ length: count }, (_, index) => `offer-${index}`);

  it('asks for at most 24 slugs a call', () => {
    expect(SAVED_OFFERS_REQUEST_LIMIT).toBe(24);
  });

  it('stops reading at the first rate-limited answer and calls every other slug unavailable', async () => {
    const lookup = vi
      .spyOn(fixtureSource, 'lookupOffer')
      .mockImplementationOnce(async () => {
        throw new CatalogueRateLimitedError();
      })
      .mockImplementation(async () => {
        await new Promise((resolve) => setTimeout(resolve, 5));
        return { kind: 'not_found' };
      });

    const result = dataOf(await loadSavedOffersAction(manySlugs(24)));

    // Only the four reads that were already under way finish; nothing is started after the 429.
    expect(lookup).toHaveBeenCalledTimes(4);
    expect(result.missing).toHaveLength(3);
    expect(result.unavailable).toHaveLength(21);
    expect(result.offers).toEqual([]);
  });

  it('budgets backend reads, not calls: a known address gets 72 reads a minute, the next is refused with no lookup', async () => {
    ip.read.mockResolvedValue('203.0.113.7');
    const lookup = vi.spyOn(fixtureSource, 'lookupOffer').mockResolvedValue({ kind: 'not_found' });
    for (const count of [24, 24, 24]) {
      expect((await loadSavedOffersAction(manySlugs(count))).success).toBe(true);
    }
    lookup.mockClear();
    const today = vi.spyOn(fixtureSource, 'getToday');

    const refused = await loadSavedOffersAction(['some-offer']);

    expect(refused).toEqual({ success: false, error: expect.any(String) });
    expect(lookup).not.toHaveBeenCalled();
    expect(today).not.toHaveBeenCalled();
    ip.read.mockResolvedValue('203.0.113.8');
    expect((await loadSavedOffersAction(['some-offer'])).success).toBe(true);
  });

  it('with no trusted address, shares 60 reads a minute in total: 60 pass, the 61st is refused with no lookup and no date read', async () => {
    const lookup = vi.spyOn(fixtureSource, 'lookupOffer').mockResolvedValue({ kind: 'not_found' });
    for (const count of [24, 24, 12]) {
      expect((await loadSavedOffersAction(manySlugs(count))).success).toBe(true);
    }
    lookup.mockClear();
    const today = vi.spyOn(fixtureSource, 'getToday');

    const refused = await loadSavedOffersAction(['one-more']);

    expect(refused).toEqual({ success: false, error: expect.any(String) });
    expect(lookup).not.toHaveBeenCalled();
    expect(today).not.toHaveBeenCalled();
  });

  it('refuses a call that does not fit the shared budget as a whole, and charges it nothing', async () => {
    vi.spyOn(fixtureSource, 'lookupOffer').mockResolvedValue({ kind: 'not_found' });
    for (const count of [24, 24]) await loadSavedOffersAction(manySlugs(count));

    expect((await loadSavedOffersAction(manySlugs(13))).success).toBe(false);
    expect((await loadSavedOffersAction(manySlugs(12))).success).toBe(true);
  });

  it('charges one read for a one-slug call, so it does not use up a 24-slug allowance', async () => {
    vi.spyOn(fixtureSource, 'lookupOffer').mockResolvedValue({ kind: 'not_found' });
    expect((await loadSavedOffersAction(['one'])).success).toBe(true);
    expect((await loadSavedOffersAction(manySlugs(24))).success).toBe(true);
    expect((await loadSavedOffersAction(manySlugs(24))).success).toBe(true);
    // 1 + 24 + 24 = 49 so far: 11 reads are left of the 60.
    expect((await loadSavedOffersAction(manySlugs(11))).success).toBe(true);
    expect((await loadSavedOffersAction(['one'])).success).toBe(false);
  });

  it('charges the deduplicated slugs, not the list as sent', async () => {
    vi.spyOn(fixtureSource, 'lookupOffer').mockResolvedValue({ kind: 'not_found' });
    for (let call = 0; call < 10; call += 1) {
      expect((await loadSavedOffersAction(Array(24).fill('same-offer'))).success).toBe(true);
    }
  });

  it('leaves a known address untouched when the shared bucket is spent', async () => {
    vi.spyOn(fixtureSource, 'lookupOffer').mockResolvedValue({ kind: 'not_found' });
    for (const count of [24, 24, 12]) await loadSavedOffersAction(manySlugs(count));
    expect((await loadSavedOffersAction(['x'])).success).toBe(false);

    ip.read.mockResolvedValue('203.0.113.7');

    expect((await loadSavedOffersAction(manySlugs(24))).success).toBe(true);
  });

  it('puts two addresses of one IPv6 /64 in one bucket, and an IPv4-mapped address with its IPv4 one', async () => {
    vi.spyOn(fixtureSource, 'lookupOffer').mockResolvedValue({ kind: 'not_found' });

    ip.read.mockResolvedValue('2001:db8:1:2::1');
    for (const count of [24, 24, 24]) await loadSavedOffersAction(manySlugs(count));
    ip.read.mockResolvedValue('2001:db8:1:2:ffff:ffff:ffff:ffff');
    expect((await loadSavedOffersAction(['x'])).success).toBe(false);
    ip.read.mockResolvedValue('2001:db8:1:3::1');
    expect((await loadSavedOffersAction(['x'])).success).toBe(true);

    ip.read.mockResolvedValue('203.0.113.7');
    for (const count of [24, 24, 24]) await loadSavedOffersAction(manySlugs(count));
    ip.read.mockResolvedValue('::ffff:203.0.113.7');
    expect((await loadSavedOffersAction(['x'])).success).toBe(false);
  });

  it('warns once per process, in production, that it has no client address, and never names one', async () => {
    vi.stubEnv('NODE_ENV', 'production');
    vi.spyOn(fixtureSource, 'lookupOffer').mockResolvedValue({ kind: 'not_found' });
    vi.resetModules();
    const { loadSavedOffersAction: fresh } = await import('@/app/actions/saved-actions');

    await fresh(['one']);
    await fresh(['two']);

    const unknownWarnings = logger.warn.mock.calls.filter(([message]) =>
      /client address/i.test(String(message))
    );
    expect(unknownWarnings).toHaveLength(1);
    vi.unstubAllEnvs();
  });

  it('does not warn about a missing client address outside production, or when there is one', async () => {
    await loadSavedOffersAction(['one']);
    ip.read.mockResolvedValue('203.0.113.7');
    vi.stubEnv('NODE_ENV', 'production');
    vi.resetModules();
    const { loadSavedOffersAction: fresh } = await import('@/app/actions/saved-actions');
    await fresh(['one']);

    expect(logger.warn).not.toHaveBeenCalled();
    vi.unstubAllEnvs();
  });

  it('logs a count-only warning for a refusal: no address, no slug', async () => {
    ip.read.mockResolvedValue('203.0.113.7');
    vi.spyOn(fixtureSource, 'lookupOffer').mockResolvedValue({ kind: 'not_found' });
    for (const count of [24, 24, 24]) await loadSavedOffersAction(manySlugs(count));
    logger.warn.mockClear();

    await loadSavedOffersAction(['very-private-slug']);

    expect(logger.warn).toHaveBeenCalledTimes(1);
    const logged = JSON.stringify(logger.warn.mock.calls);
    expect(logged).toMatch(/reads/);
    expect(logged).not.toContain('203.0.113.7');
    expect(logged).not.toContain('very-private-slug');
  });

  it('stops after 4 unavailable results in a row, however many answers came earlier', async () => {
    let calls = 0;
    const lookup = vi.spyOn(fixtureSource, 'lookupOffer').mockImplementation(async () => {
      calls += 1;
      if (calls === 1) return { kind: 'found', offer: live[0] };
      throw new CatalogueUnavailableError();
    });

    const result = dataOf(await loadSavedOffersAction(manySlugs(24)));

    // One answer, then the backend fell over: at most 1 + 7 reads, not 24.
    expect(lookup.mock.calls.length).toBeLessThanOrEqual(8);
    expect(result.unavailable.length).toBeGreaterThanOrEqual(16);
  });

  it('keeps reading when failures are scattered among answers', async () => {
    let calls = 0;
    const lookup = vi.spyOn(fixtureSource, 'lookupOffer').mockImplementation(async () => {
      calls += 1;
      if (calls % 3 === 0) throw new CatalogueUnavailableError();
      return { kind: 'not_found' };
    });

    const result = dataOf(await loadSavedOffersAction(manySlugs(24)));

    expect(lookup).toHaveBeenCalledTimes(24);
    expect(result.unavailable).toHaveLength(8);
    expect(result.missing).toHaveLength(16);
  });

  it('with every read failing, 24 slugs cost at most 8 reads', async () => {
    const lookup = vi
      .spyOn(fixtureSource, 'lookupOffer')
      .mockRejectedValue(new CatalogueUnavailableError());

    const result = dataOf(await loadSavedOffersAction(manySlugs(24)));

    expect(lookup.mock.calls.length).toBeLessThanOrEqual(8);
    expect(result.unavailable).toHaveLength(24);
    expect(result.missing).toEqual([]);
  });

  it('after about 8 seconds takes no new slug and calls the rest unavailable', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    const lookup = vi.spyOn(fixtureSource, 'lookupOffer').mockImplementation(async () => {
      vi.setSystemTime(Date.now() + 9_000);
      return { kind: 'not_found' };
    });

    const result = dataOf(await loadSavedOffersAction(manySlugs(24)));

    // The first read burns the whole budget; no later slug is asked about.
    expect(lookup.mock.calls.length).toBeLessThanOrEqual(4);
    expect(result.missing.length + result.unavailable.length).toBe(24);
    expect(result.unavailable.length).toBeGreaterThanOrEqual(20);
  });
});

describe('loadSavedOffersAction: what counts as gone', () => {
  it('calls an offer the catalogue could not read unavailable, never missing', async () => {
    vi.spyOn(fixtureSource, 'lookupOffer').mockResolvedValue({ kind: 'unreadable' });

    const result = dataOf(await loadSavedOffersAction(['new-kind-of-offer']));

    expect(result.unavailable).toEqual(['new-kind-of-offer']);
    expect(result.missing).toEqual([]);
  });

  it('calls an answer for a different slug unavailable (a fault, not a definitive no), never a card for the wrong offer', async () => {
    vi.spyOn(fixtureSource, 'lookupOffer').mockResolvedValue({ kind: 'found', offer: live[0] });

    const result = dataOf(await loadSavedOffersAction(['some-other-slug']));

    expect(result.offers).toEqual([]);
    expect(result.unavailable).toEqual(['some-other-slug']);
    expect(result.missing).toEqual([]);
  });

  it('still calls a found offer that is no longer live missing: that is a definitive answer', async () => {
    const pastEnd = { ...live[0], endAt: '2026-10-01' };
    vi.spyOn(fixtureSource, 'lookupOffer').mockResolvedValue({ kind: 'found', offer: pastEnd });

    const result = dataOf(await loadSavedOffersAction([pastEnd.slug]));

    expect(result.missing).toEqual([pastEnd.slug]);
  });
});

describe('loadSavedOffersAction: only what a card needs leaves the server', () => {
  it('returns a card-shaped projection, not the whole offer', async () => {
    const result = dataOf(await loadSavedOffersAction([live[0].slug]));

    expect(Object.keys(result.offers[0]).sort()).toEqual(
      [
        'demo',
        'freshness',
        'id',
        'image',
        'merchant',
        'merchantSlug',
        'offerType',
        'postage',
        'slug',
        'source',
        'title',
      ].sort()
    );
  });
});
