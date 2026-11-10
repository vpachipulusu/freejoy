// @vitest-environment node
/** The Saved action on the real API source: a record this site cannot read must not be pruned. */
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ fetchOffer: vi.fn() }));
vi.mock('@/lib/api/catalogue', () => ({
  fetchOffer: mocks.fetchOffer,
  fetchOffers: vi.fn(),
  fetchCategories: vi.fn(),
}));
vi.mock('@/lib/logger', () => ({ logger: { warn: vi.fn(), error: vi.fn(), info: vi.fn() } }));
vi.mock('@/lib/api/server', () => ({ readTrustedClientIp: vi.fn(async () => undefined) }));

import { loadSavedOffersAction } from '@/app/actions/saved-actions';
import { resetRateLimitStore } from '@/lib/middleware/rate-limit';

const apiOffer = (slug: string, patch: Record<string, unknown> = {}) => ({
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

beforeEach(() => {
  vi.stubEnv('CATALOGUE_SOURCE', 'api');
  mocks.fetchOffer.mockReset();
  resetRateLimitStore();
});

describe('loadSavedOffersAction on the API source', () => {
  it('keeps an offer whose record this site cannot read (an unknown freshness) as unavailable', async () => {
    mocks.fetchOffer.mockResolvedValue({
      kind: 'found',
      offer: apiOffer('new-kind', { freshness: 'brand_new' }),
    });

    const result = await loadSavedOffersAction(['new-kind']);

    expect(result).toEqual({
      success: true,
      data: { offers: [], missing: [], unavailable: ['new-kind'] },
    });
  });

  it('still calls a real 404 and a 410 missing, and returns a readable offer as a card', async () => {
    mocks.fetchOffer.mockImplementation(async (slug: string) => {
      if (slug === 'gone') return { kind: 'gone', ended: null };
      if (slug === 'nope') return { kind: 'not_found' };
      return { kind: 'found', offer: apiOffer(slug) };
    });

    const result = await loadSavedOffersAction(['free-tea', 'gone', 'nope']);

    expect(result.success && result.data.missing).toEqual(['gone', 'nope']);
    expect(result.success && result.data.offers.map((offer) => offer.slug)).toEqual(['free-tea']);
  });
});
