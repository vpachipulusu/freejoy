/**
 * FREEJ-60: `earns_commission` crosses the API boundary as sent. When it is absent or not a boolean the offer is
 * still shown (it is a disclosure, not a reason to hide an offer) and treated as earning: over-disclosing the "Ad"
 * label is the safe side under UK ASA rules.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ warn: vi.fn() }));
vi.mock('@/lib/logger', () => ({ logger: { warn: mocks.warn } }));

import type { ApiOffer, ApiOfferDetail } from '@/lib/api/catalogue';
import { mapOffer, mapOffers } from '@/lib/catalogue/api-mapper';

const apiOffer = (patch: Record<string, unknown> = {}): ApiOfferDetail =>
  ({
    id: 'offer-1',
    slug: 'free-tea-sample',
    title: 'Free tea sample',
    summary: 'A taster',
    description: ['One.'],
    category: { slug: 'food-drink', name: 'Food & drink' },
    merchant: { slug: 'tea-time', name: 'Tea Time', logo_url: null },
    offer_type: 'free_sample',
    sponsored: false,
    earns_commission: true,
    featured: false,
    image: { tint: 'mint', label: 'cuppa', alt: 'A cup of tea' },
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
  }) as ApiOfferDetail;

beforeEach(() => vi.clearAllMocks());

describe('earns_commission in the API mapper', () => {
  it.each([true, false])('carries earns_commission: %s through', (flag) => {
    expect(mapOffer(apiOffer({ earns_commission: flag }))?.earnsCommission).toBe(flag);
  });

  it('is independent of sponsored', () => {
    const offer = mapOffer(apiOffer({ sponsored: true, earns_commission: false }));

    expect(offer?.sponsored).toBe(true);
    expect(offer?.earnsCommission).toBe(false);
  });

  it.each([undefined, null, 'false', 0, 1, {}])(
    'treats an absent or malformed flag (%j) as earning, and keeps the offer',
    (flag) => {
      const offer = mapOffer(apiOffer({ earns_commission: flag }));

      expect(offer).not.toBeNull();
      expect(offer?.earnsCommission).toBe(true);
    }
  );

  it('treats a missing field as earning (an older backend, or a list item that never carries it)', () => {
    const { earns_commission: _omitted, ...withoutFlag } = apiOffer();

    expect(mapOffer(withoutFlag as ApiOffer)?.earnsCommission).toBe(true);
  });

  it('maps a list page, whose items carry no flag, to earning offers without dropping any', () => {
    const items = [apiOffer({ slug: 'one' }), apiOffer({ slug: 'two' })].map((item) => {
      const { earns_commission: _omitted, ...listItem } = item;
      return listItem as ApiOffer;
    });

    const offers = mapOffers(items);

    expect(offers.map((offer) => offer.earnsCommission)).toEqual([true, true]);
    expect(mocks.warn).not.toHaveBeenCalled();
  });
});
