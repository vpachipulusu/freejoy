// @vitest-environment node
/** The API-backed catalogue: mapping, paging, memoising and what happens when the wrapper throws. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ApiCategory, ApiOffer } from '@/lib/api/catalogue';

const mocks = vi.hoisted(() => ({
  fetchOffers: vi.fn(),
  fetchOffer: vi.fn(),
  fetchCategories: vi.fn(),
  warn: vi.fn(),
}));

vi.mock('@/lib/api/catalogue', () => ({
  fetchOffers: mocks.fetchOffers,
  fetchOffer: mocks.fetchOffer,
  fetchCategories: mocks.fetchCategories,
}));
vi.mock('@/lib/logger', () => ({ logger: { warn: mocks.warn } }));

import { apiSource } from '@/lib/catalogue/api-source';
import { CatalogueUnavailableError } from '@/lib/catalogue/errors';
import { DEFAULT_QUERY } from '@/lib/catalogue/offer-url';
import type { CatalogueQuery } from '@/lib/catalogue/types';

const apiOffer = (slug: string, patch: Partial<ApiOffer> = {}): ApiOffer => ({
  id: `id-${slug}`,
  slug,
  title: `Title ${slug}`,
  summary: 'Summary',
  description: ['Paragraph'],
  category: { slug: 'food-drink', name: 'Food & drink' },
  merchant: { slug: 'tea-time', name: 'Tea Time', logo_url: null },
  offer_type: 'free_sample',
  sponsored: false,
  featured: false,
  image: { tint: 'mint', label: 'l', alt: 'a' },
  postage: { kind: 'free', amount_minor: null, currency: null },
  requires_card: false,
  subscription_terms: null,
  eligibility: 'UK',
  availability: 'Now',
  start_at: '2026-10-01T09:00:00Z',
  end_at: null,
  last_verified_at: '2026-10-28T10:00:00Z',
  freshness: null,
  steps: [],
  ...patch,
});

const apiCategory = (slug: string, sort: number): ApiCategory => ({
  slug,
  name: slug,
  short_name: slug,
  description: 'd',
  intro: 'i',
  tint: 'sage',
  sort_order: sort,
  offer_count: 1,
});

const list = (slugs: string[], total = slugs.length) => ({
  items: slugs.map((slug) => apiOffer(slug)),
  total,
  limit: 12,
  offset: 0,
});

const query = (patch: Partial<CatalogueQuery> = {}): CatalogueQuery => ({
  ...DEFAULT_QUERY,
  ...patch,
});

beforeEach(() => {
  for (const mock of Object.values(mocks)) mock.mockReset();
});

describe('categories', () => {
  it('maps, sorts and drops categories with unsafe slugs', async () => {
    mocks.fetchCategories.mockResolvedValue([
      apiCategory('pets', 2),
      apiCategory('Bad Slug', 0),
      apiCategory('beauty', 1),
    ]);

    const categories = await apiSource.getCategories();

    expect(categories.map((category) => category.slug)).toEqual(['beauty', 'pets']);
    expect(await apiSource.getCategoryBySlug('pets')).toMatchObject({ name: 'pets' });
    expect(await apiSource.getCategoryBySlug('cars')).toBeNull();
  });

  it('answers null for a slug that is not a plain slug, without asking the API', async () => {
    expect(await apiSource.getCategoryBySlug('../admin')).toBeNull();
    expect(mocks.fetchCategories).not.toHaveBeenCalled();
  });

  it('lets an outage through as the typed error', async () => {
    mocks.fetchCategories.mockRejectedValue(new CatalogueUnavailableError());

    await expect(apiSource.getCategories()).rejects.toBeInstanceOf(CatalogueUnavailableError);
  });
});

describe('merchants', () => {
  it('has no merchant list: offers carry their own', async () => {
    expect(await apiSource.getMerchants()).toEqual([]);
    expect(await apiSource.getMerchantBySlug('tea-time')).toBeNull();
  });
});

describe('searchOffers', () => {
  it('asks for one page with the filters, as the page and offset the API expects', async () => {
    mocks.fetchOffers.mockResolvedValue(list(['a', 'b'], 30));

    const page = await apiSource.searchOffers(
      query({
        q: '  green   tea ',
        category: 'food-drink',
        types: ['voucher'],
        postage: 'free',
        sort: 'ending_soon',
        page: 2,
      })
    );

    expect(mocks.fetchOffers).toHaveBeenCalledWith({
      q: 'green tea',
      category: 'food-drink',
      types: ['voucher'],
      postage: 'free',
      sort: 'ending_soon',
      limit: 12,
      offset: 12,
    });
    expect(page).toMatchObject({ page: 2, pageCount: 3, total: 30, from: 13, to: 14 });
    expect(page.items.map((offer) => offer.slug)).toEqual(['a', 'b']);
    expect(page.items[0]).toMatchObject({ source: 'api', demo: false });
  });

  it('leaves out filters that are not set', async () => {
    mocks.fetchOffers.mockResolvedValue(list([], 0));

    await apiSource.searchOffers(query());

    expect(mocks.fetchOffers).toHaveBeenCalledWith({
      types: [],
      sort: 'newest',
      limit: 12,
      offset: 0,
    });
  });

  it('returns an empty page with a real total of zero', async () => {
    mocks.fetchOffers.mockResolvedValue(list([], 0));

    expect(await apiSource.searchOffers(query())).toEqual({
      items: [],
      page: 1,
      pageCount: 1,
      total: 0,
      from: 0,
      to: 0,
    });
  });

  it('shows the last page when the requested page is past the end', async () => {
    mocks.fetchOffers.mockResolvedValueOnce(list([], 25)).mockResolvedValueOnce(list(['z'], 25));

    const page = await apiSource.searchOffers(query({ page: 99 }));

    expect(mocks.fetchOffers.mock.calls[1][0]).toMatchObject({ offset: 24 });
    expect(page).toMatchObject({ page: 3, pageCount: 3, total: 25, from: 25, to: 25 });
  });

  it('never asks for an offset the API refuses', async () => {
    mocks.fetchOffers.mockResolvedValue(list([], 0));

    await apiSource.searchOffers(query({ page: 999 }));

    expect(mocks.fetchOffers.mock.calls[0][0].offset).toBeLessThanOrEqual(10_000);
  });

  it('drops an offer the mapper rejects but keeps the API total', async () => {
    mocks.fetchOffers.mockResolvedValue({
      items: [apiOffer('good'), apiOffer('BAD SLUG')],
      total: 2,
      limit: 12,
      offset: 0,
    });

    const page = await apiSource.searchOffers(query());

    expect(page.items.map((offer) => offer.slug)).toEqual(['good']);
    expect(page.total).toBe(2);
    expect(mocks.warn).toHaveBeenCalledTimes(1);
  });

  it('lets an outage through when every offer on a non-empty page is unreadable, never an empty page', async () => {
    mocks.fetchOffers.mockResolvedValue({
      items: [apiOffer('one', { sponsored: undefined }), apiOffer('two', { sponsored: undefined })],
      total: 2,
      limit: 12,
      offset: 0,
    });

    await expect(apiSource.searchOffers(query())).rejects.toBeInstanceOf(CatalogueUnavailableError);
  });

  it('answers an empty page for a category that is not a slug, without asking the API', async () => {
    const page = await apiSource.searchOffers(query({ category: 'Not A Slug' }));

    expect(page.total).toBe(0);
    expect(mocks.fetchOffers).not.toHaveBeenCalled();
  });

  it('lets an outage through instead of an empty list', async () => {
    mocks.fetchOffers.mockRejectedValue(new CatalogueUnavailableError());

    await expect(apiSource.searchOffers(query())).rejects.toBeInstanceOf(CatalogueUnavailableError);
  });
});

describe('featured and live offers', () => {
  it('asks for featured offers newest first, within the API limit', async () => {
    mocks.fetchOffers.mockResolvedValue(list(['a']));

    await apiSource.getFeaturedOffers(8);
    await apiSource.getFeaturedOffers(500);

    expect(mocks.fetchOffers.mock.calls[0][0]).toEqual({
      featured: true,
      sort: 'newest',
      limit: 8,
      offset: 0,
    });
    expect(mocks.fetchOffers.mock.calls[1][0].limit).toBe(50);
  });

  it('lets an outage through when every featured or live offer on a page is unreadable', async () => {
    const unreadable = {
      items: [apiOffer('one', { sponsored: undefined })],
      total: 1,
      limit: 12,
      offset: 0,
    };
    mocks.fetchOffers.mockResolvedValue(unreadable);

    await expect(apiSource.getFeaturedOffers(4)).rejects.toBeInstanceOf(CatalogueUnavailableError);
    await expect(apiSource.getLiveOffers()).rejects.toBeInstanceOf(CatalogueUnavailableError);
  });

  it('asks for nothing when the limit is zero or negative', async () => {
    expect(await apiSource.getFeaturedOffers(0)).toEqual([]);
    expect(await apiSource.getFeaturedOffers(-3)).toEqual([]);
    expect(mocks.fetchOffers).not.toHaveBeenCalled();
  });

  it('walks every page of live offers until it has them all', async () => {
    const first = Array.from({ length: 50 }, (_, index) => `a${index}`);
    const second = Array.from({ length: 20 }, (_, index) => `b${index}`);
    mocks.fetchOffers
      .mockResolvedValueOnce(list(first, 70))
      .mockResolvedValueOnce(list(second, 70));

    const offers = await apiSource.getLiveOffers();

    expect(offers).toHaveLength(70);
    expect(mocks.fetchOffers).toHaveBeenCalledTimes(2);
    expect(mocks.fetchOffers.mock.calls[1][0]).toMatchObject({ offset: 50, limit: 50 });
  });

  it('stops after one request when everything fits in a page, or nothing is live', async () => {
    mocks.fetchOffers.mockResolvedValueOnce(list(['a', 'b'], 2));
    expect(await apiSource.getLiveOffers()).toHaveLength(2);
    expect(mocks.fetchOffers).toHaveBeenCalledTimes(1);

    mocks.fetchOffers.mockReset();
    mocks.fetchOffers.mockResolvedValueOnce(list([], 0));
    expect(await apiSource.getLiveOffers()).toEqual([]);
    expect(mocks.fetchOffers).toHaveBeenCalledTimes(1);
  });

  it('caps the walk so a huge catalogue cannot loop for ever', async () => {
    mocks.fetchOffers.mockResolvedValue(
      list(
        Array.from({ length: 50 }, (_, i) => `s${i}`),
        99999
      )
    );

    await apiSource.getLiveOffers();

    expect(mocks.fetchOffers).toHaveBeenCalledTimes(40);
  });
});

describe('getRelatedOffers', () => {
  const current = async () => {
    mocks.fetchOffer.mockResolvedValue({ kind: 'found', offer: apiOffer('current') });
    const lookup = await apiSource.lookupOffer('current');
    if (lookup.kind !== 'found') throw new Error('expected a found offer');
    return lookup.offer;
  };

  it('uses same-category offers, never the offer itself', async () => {
    const offer = await current();
    mocks.fetchOffers.mockResolvedValue(list(['current', 'x', 'y', 'z', 'w']));

    const related = await apiSource.getRelatedOffers(offer, 4);

    expect(related.map((entry) => entry.slug)).toEqual(['x', 'y', 'z', 'w']);
    expect(mocks.fetchOffers).toHaveBeenCalledTimes(1);
    expect(mocks.fetchOffers.mock.calls[0][0]).toEqual({ category: 'food-drink', limit: 5 });
  });

  it('pads with featured offers from other categories', async () => {
    const offer = await current();
    mocks.fetchOffers.mockResolvedValueOnce(list(['x'])).mockResolvedValueOnce({
      items: [
        apiOffer('same-cat'),
        apiOffer('other-1', { category: { slug: 'pets', name: 'Pets' } }),
        apiOffer('other-2', { category: { slug: 'beauty', name: 'Beauty' } }),
      ],
      total: 3,
      limit: 12,
      offset: 0,
    });

    const related = await apiSource.getRelatedOffers(offer, 3);

    expect(related.map((entry) => entry.slug)).toEqual(['x', 'other-1', 'other-2']);
    expect(mocks.fetchOffers.mock.calls[1][0]).toEqual({ featured: true, limit: 12 });
  });

  it('asks for nothing when the limit is not positive', async () => {
    expect(await apiSource.getRelatedOffers(await current(), 0)).toEqual([]);
  });
});

describe('lookupOffer', () => {
  it('maps a found offer', async () => {
    mocks.fetchOffer.mockResolvedValue({ kind: 'found', offer: apiOffer('free-tea') });

    const lookup = await apiSource.lookupOffer('free-tea');

    expect(lookup).toMatchObject({ kind: 'found', offer: { slug: 'free-tea', source: 'api' } });
  });

  it('maps the 410 stub to an ended offer', async () => {
    mocks.fetchOffer.mockResolvedValue({
      kind: 'gone',
      ended: {
        slug: 'old-tea',
        title: 'Old tea',
        category: { slug: 'food-drink', name: 'Food & drink' },
        ended_at: '2026-05-30T12:00:00Z',
      },
    });

    expect(await apiSource.lookupOffer('old-tea')).toEqual({
      kind: 'ended',
      ended: {
        slug: 'old-tea',
        title: 'Old tea',
        categorySlug: 'food-drink',
        categoryName: 'Food & drink',
        endedAt: '2026-05-30',
      },
    });
  });

  it('says unreadable, not not-found, for an offer the mapper cannot read (a value this site does not know yet)', async () => {
    mocks.fetchOffer.mockResolvedValueOnce({
      kind: 'found',
      offer: apiOffer('new-kind', { freshness: 'brand_new' as never }),
    });
    expect(await apiSource.lookupOffer('new-kind')).toEqual({ kind: 'unreadable' });

    mocks.fetchOffer.mockResolvedValueOnce({
      kind: 'found',
      offer: apiOffer('bad-dates-2', { last_verified_at: null }),
    });
    expect(await apiSource.lookupOffer('bad-dates-2')).toEqual({ kind: 'unreadable' });
  });

  it('says not found for a 404 and for a 410 with no usable stub', async () => {
    mocks.fetchOffer.mockResolvedValueOnce({ kind: 'not_found' });
    expect(await apiSource.lookupOffer('missing')).toEqual({ kind: 'not_found' });

    mocks.fetchOffer.mockResolvedValueOnce({ kind: 'gone', ended: null });
    expect(await apiSource.lookupOffer('gone-1')).toEqual({ kind: 'not_found' });
  });

  it('does not call the API for a slug that cannot name an offer', async () => {
    for (const slug of ['Bad Slug', '../x', '', 'a/b']) {
      expect(await apiSource.lookupOffer(slug)).toEqual({ kind: 'not_found' });
    }
    expect(mocks.fetchOffer).not.toHaveBeenCalled();
  });

  it('lets an outage through rather than calling the offer missing', async () => {
    mocks.fetchOffer.mockRejectedValue(new CatalogueUnavailableError());

    await expect(apiSource.lookupOffer('free-tea')).rejects.toBeInstanceOf(
      CatalogueUnavailableError
    );
  });
});

describe('getToday', () => {
  afterEach(() => vi.useRealTimers());

  it('is the Europe/London calendar date, not the UTC date', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-07-01T23:30:00Z'));

    expect(await apiSource.getToday()).toBe('2026-07-02');

    vi.setSystemTime(new Date('2026-01-01T23:30:00Z'));
    expect(await apiSource.getToday()).toBe('2026-01-01');
  });
});
