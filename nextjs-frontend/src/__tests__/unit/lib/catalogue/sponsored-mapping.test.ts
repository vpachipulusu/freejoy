/** The sponsored flag crosses the API boundary: mapped as it is sent, never guessed when it is missing. */
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ warn: vi.fn() }));
vi.mock('@/lib/logger', () => ({ logger: { warn: mocks.warn } }));

import type { ApiOffer } from '@/lib/api/catalogue';
import { mapOffer } from '@/lib/catalogue/api-mapper';
import { toCardOffer } from '@/lib/catalogue/types';

const apiOffer = (patch: Record<string, unknown> = {}): ApiOffer =>
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
  }) as ApiOffer;

beforeEach(() => vi.clearAllMocks());

describe('sponsored in the API mapper', () => {
  it.each([true, false])('carries sponsored: %s through', (flag) => {
    expect(mapOffer(apiOffer({ sponsored: flag }))?.sponsored).toBe(flag);
  });

  it.each([undefined, null, 'true', 1])(
    'drops (and logs) an offer whose sponsored flag is %j rather than show it unlabelled',
    (flag) => {
      expect(mapOffer(apiOffer({ sponsored: flag }))).toBeNull();
      expect(mocks.warn).toHaveBeenCalledTimes(1);
    }
  );

  it('puts sponsored on the card projection and nothing else about the basis', () => {
    const card = toCardOffer(mapOffer(apiOffer({ sponsored: true }))!);

    expect(card.sponsored).toBe(true);
    expect(Object.keys(card)).not.toContain('commercialBasis');
  });
});
