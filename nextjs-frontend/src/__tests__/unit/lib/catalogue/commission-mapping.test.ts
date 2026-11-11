/**
 * FREEJ-60: `earns_commission` crosses the API boundary as sent, on the offer DETAIL only. When it is absent or not a
 * boolean the offer is still shown and the flag stays unknown (null) with one warning; the page treats unknown as
 * "disclose" (over-disclosing the "Ad" label is the safe side under UK ASA rules). A list item never carries it.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ warn: vi.fn() }));
vi.mock('@/lib/logger', () => ({ logger: { warn: mocks.warn } }));

import type { ApiOffer, ApiOfferDetail } from '@/lib/api/catalogue';
import { mapOffer, mapOfferDetail, mapOffers } from '@/lib/catalogue/api-mapper';

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

const listItem = (patch: Record<string, unknown> = {}): ApiOffer => {
  const { earns_commission: _omitted, ...rest } = apiOffer(patch);
  return rest as ApiOffer;
};

beforeEach(() => vi.clearAllMocks());

describe('earns_commission in the detail mapper', () => {
  it.each([true, false])('carries earns_commission: %s through, with no warning', (flag) => {
    expect(mapOfferDetail(apiOffer({ earns_commission: flag }))?.earnsCommission).toBe(flag);
    expect(mocks.warn).not.toHaveBeenCalled();
  });

  it('is independent of sponsored', () => {
    const offer = mapOfferDetail(apiOffer({ sponsored: true, earns_commission: false }));

    expect(offer?.sponsored).toBe(true);
    expect(offer?.earnsCommission).toBe(false);
  });

  it.each([undefined, null, 'false', 0, 1, {}])(
    'keeps the offer and maps an absent or malformed flag (%j) to null, with one warning naming the slug only',
    (flag) => {
      const offer = mapOfferDetail(apiOffer({ earns_commission: flag }));

      expect(offer).not.toBeNull();
      expect(offer?.earnsCommission).toBeNull();
      expect(mocks.warn).toHaveBeenCalledTimes(1);
      expect(mocks.warn.mock.calls[0][1]).toEqual({ slug: 'free-tea-sample' });
    }
  );

  it('maps a field that is missing altogether to null with one warning', () => {
    const { earns_commission: _omitted, ...withoutFlag } = apiOffer();

    expect(mapOfferDetail(withoutFlag as ApiOfferDetail)?.earnsCommission).toBeNull();
    expect(mocks.warn).toHaveBeenCalledTimes(1);
  });

  it('drops a record that is unreadable for another reason, with that reason only', () => {
    expect(mapOfferDetail(apiOffer({ sponsored: undefined }))).toBeNull();
    expect(mocks.warn).toHaveBeenCalledTimes(1);
  });
});

describe('list items', () => {
  it('map to an unknown flag (null) with no warning: the list does not carry it', () => {
    expect(mapOffer(listItem())?.earnsCommission).toBeNull();

    const offers = mapOffers([listItem({ slug: 'one' }), listItem({ slug: 'two' })]);

    expect(offers.map((offer) => offer.earnsCommission)).toEqual([null, null]);
    expect(mocks.warn).not.toHaveBeenCalled();
  });
});
