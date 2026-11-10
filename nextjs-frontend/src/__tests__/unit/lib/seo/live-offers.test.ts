import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Offer } from '@/lib/catalogue/types';

const mocks = vi.hoisted(() => ({
  liveOffers: vi.fn(),
  categories: vi.fn(),
  logError: vi.fn(),
}));

vi.mock('@/lib/logger', () => ({ logger: { error: mocks.logError } }));
vi.mock('@/lib/catalogue', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/catalogue')>();
  return {
    ...actual,
    getLiveOffers: () => mocks.liveOffers(),
    getCategories: () =>
      mocks.categories.getMockImplementation() ? mocks.categories() : actual.getCategories(),
  };
});

import sitemap from '@/app/sitemap';
import { OFFERS } from '@/lib/catalogue/fixtures';
import { loadCategoriesForSeo, loadLiveOffersForSeo } from '@/lib/seo/live-offers';

const apiOffer = { ...(OFFERS[0] as Offer), source: 'api', demo: false } as Offer;

beforeEach(() => {
  mocks.liveOffers.mockReset();
  mocks.categories.mockReset();
  mocks.logError.mockClear();
});

describe('loadLiveOffersForSeo', () => {
  it('returns the live offers untouched when the read works', async () => {
    mocks.liveOffers.mockResolvedValue([apiOffer]);

    expect(await loadLiveOffersForSeo('test')).toEqual([apiOffer]);
    expect(mocks.logError).not.toHaveBeenCalled();
  });

  it('logs the context and message, then returns an empty list when the read rejects', async () => {
    mocks.liveOffers.mockRejectedValue(new Error('catalogue down'));

    expect(await loadLiveOffersForSeo('unit')).toEqual([]);
    expect(mocks.logError).toHaveBeenCalledWith(expect.any(String), {
      context: 'unit',
      error: 'catalogue down',
    });
  });

  it('copes with a rejection that is not an Error', async () => {
    mocks.liveOffers.mockRejectedValue('boom');

    expect(await loadLiveOffersForSeo('unit')).toEqual([]);
    expect(mocks.logError).toHaveBeenCalledWith(expect.any(String), {
      context: 'unit',
      error: 'unknown error',
    });
  });
});

describe('loadCategoriesForSeo', () => {
  it('returns the categories when the read works', async () => {
    expect((await loadCategoriesForSeo('test')).length).toBeGreaterThan(0);
    expect(mocks.logError).not.toHaveBeenCalled();
  });

  it('logs and returns an empty list when the read rejects', async () => {
    mocks.categories.mockRejectedValue(new Error('categories down'));

    expect(await loadCategoriesForSeo('unit')).toEqual([]);
    expect(mocks.logError).toHaveBeenCalledWith(expect.any(String), {
      context: 'unit',
      error: 'categories down',
    });
  });

  it('copes with a rejection that is not an Error', async () => {
    mocks.categories.mockRejectedValue('boom');

    expect(await loadCategoriesForSeo('unit')).toEqual([]);
    expect(mocks.logError).toHaveBeenCalledWith(expect.any(String), {
      context: 'unit',
      error: 'unknown error',
    });
  });
});

describe('sitemap when there are no usable live offers', () => {
  const staticPages = ['http://localhost:3000/', 'http://localhost:3000/freebies'];

  it('lists only the static pages when the live list is empty', async () => {
    mocks.liveOffers.mockResolvedValue([]);

    expect((await sitemap()).map((entry) => entry.url)).toEqual(staticPages);
  });

  it('lists only the static pages, and does not throw, when the catalogue read rejects', async () => {
    mocks.liveOffers.mockRejectedValue(new Error('catalogue down'));

    expect((await sitemap()).map((entry) => entry.url)).toEqual(staticPages);
    expect(mocks.logError).toHaveBeenCalledTimes(1);
  });

  it('lists only the static pages, and does not throw, when the categories cannot be read', async () => {
    mocks.liveOffers.mockResolvedValue([]);
    mocks.categories.mockRejectedValue(new Error('categories down'));

    expect((await sitemap()).map((entry) => entry.url)).toEqual(staticPages);
    expect(mocks.logError).toHaveBeenCalledTimes(1);
  });
});
