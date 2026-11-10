// @vitest-environment node
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ApiCategory, ApiEndedOffer, ApiOffer } from '@/lib/api/catalogue';

const mocks = vi.hoisted(() => ({ warn: vi.fn() }));
vi.mock('@/lib/logger', () => ({ logger: { warn: mocks.warn } }));

import { mapCategory, mapEndedOffer, mapOffer, mapOffers } from '@/lib/catalogue/api-mapper';
import { isValidSlug } from '@/lib/catalogue/slug';

const apiOffer = (patch: Partial<ApiOffer> = {}): ApiOffer => ({
  id: 'offer-1',
  slug: 'free-tea-sample',
  title: 'Free tea sample',
  summary: 'A taster of loose leaf tea',
  description: ['First paragraph.', 'Second paragraph.'],
  category: { slug: 'food-drink', name: 'Food & drink' },
  merchant: { slug: 'tea-time', name: 'Tea Time', logo_url: null },
  offer_type: 'free_sample',
  sponsored: false,
  featured: true,
  image: { tint: 'mint', label: 'cuppa', alt: 'A cup of tea' },
  postage: { kind: 'free', amount_minor: null, currency: null },
  requires_card: false,
  subscription_terms: null,
  eligibility: 'UK residents',
  availability: 'While stocks last',
  start_at: '2026-10-01T09:00:00Z',
  end_at: '2026-12-31T23:59:00Z',
  last_verified_at: '2026-10-28T10:00:00Z',
  freshness: 'just_added',
  steps: [{ title: 'Sign up', detail: 'Fill in the form' }],
  ...patch,
});

const apiCategory = (patch: Partial<ApiCategory> = {}): ApiCategory => ({
  slug: 'food-drink',
  name: 'Food & drink',
  short_name: 'Food',
  description: 'Tasty things',
  intro: 'A longer intro',
  tint: 'sage',
  sort_order: 2,
  offer_count: 5,
  ...patch,
});

beforeEach(() => mocks.warn.mockClear());

describe('isValidSlug', () => {
  it.each(['free-tea', 'a', '123', 'a-1-b'])('accepts %s', (slug) => {
    expect(isValidSlug(slug)).toBe(true);
  });

  it.each([
    '',
    'Free-Tea',
    'free tea',
    'free_tea',
    'free/tea',
    '../etc',
    'tea?x=1',
    'tea#frag',
    'tea\n',
    '%2e%2e',
    'a'.repeat(101),
  ])('rejects %j', (slug) => {
    expect(isValidSlug(slug)).toBe(false);
  });

  it('rejects values that are not strings', () => {
    expect(isValidSlug(undefined)).toBe(false);
    expect(isValidSlug(null)).toBe(false);
    expect(isValidSlug(42)).toBe(false);
  });
});

describe('mapOffer', () => {
  it('maps an API offer onto the page shape, marked as real data', () => {
    const offer = mapOffer(apiOffer());

    expect(offer).toMatchObject({
      id: 'offer-1',
      slug: 'free-tea-sample',
      title: 'Free tea sample',
      summary: 'A taster of loose leaf tea',
      description: ['First paragraph.', 'Second paragraph.'],
      categorySlug: 'food-drink',
      merchantSlug: 'tea-time',
      merchant: { slug: 'tea-time', name: 'Tea Time', logoUrl: null },
      offerType: 'free_sample',
      status: 'published',
      featured: true,
      image: { tint: 'mint', label: 'cuppa', alt: 'A cup of tea' },
      postage: { kind: 'free' },
      requiresCard: false,
      subscriptionTerms: null,
      eligibility: 'UK residents',
      availability: 'While stocks last',
      freshness: 'just_added',
      steps: [{ title: 'Sign up', detail: 'Fill in the form' }],
      demo: false,
      source: 'api',
      destinationUrl: null,
    });
    expect(mocks.warn).not.toHaveBeenCalled();
  });

  it('turns timestamps into Europe/London calendar dates', () => {
    const offer = mapOffer(
      apiOffer({
        start_at: '2026-07-01T23:30:00Z', // 00:30 on 2 July in BST
        end_at: '2026-12-31T23:59:00Z', // still 31 December in GMT
        last_verified_at: '2026-10-28T10:00:00Z',
      })
    );

    expect(offer).toMatchObject({
      startAt: '2026-07-02',
      endAt: '2026-12-31',
      lastVerifiedAt: '2026-10-28',
    });
  });

  it('keeps an open-ended offer open', () => {
    expect(mapOffer(apiOffer({ end_at: null }))?.endAt).toBeNull();
  });

  it('copies lists so the API record is never shared', () => {
    const api = apiOffer();
    const offer = mapOffer(api);

    expect(offer?.description).not.toBe(api.description);
    expect(offer?.steps).not.toBe(api.steps);
  });

  it('keeps a priced postage in pence and drops one it cannot read to check-terms', () => {
    expect(
      mapOffer(apiOffer({ postage: { kind: 'cost', amount_minor: 199, currency: 'GBP' } }))?.postage
    ).toEqual({ kind: 'cost', amountMinor: 199, currency: 'GBP' });
    expect(
      mapOffer(apiOffer({ postage: { kind: 'cost', amount_minor: 199, currency: 'USD' } }))?.postage
    ).toEqual({ kind: 'check_terms' });
    expect(
      mapOffer(apiOffer({ postage: { kind: 'cost', amount_minor: null, currency: 'GBP' } }))
        ?.postage
    ).toEqual({ kind: 'check_terms' });
    expect(
      mapOffer(apiOffer({ postage: { kind: 'check_terms', amount_minor: null, currency: null } }))
        ?.postage
    ).toEqual({ kind: 'check_terms' });
  });

  it('falls back to a neutral tint for one it does not know', () => {
    expect(mapOffer(apiOffer({ image: { tint: 'neon', label: 'x', alt: 'y' } }))?.image.tint).toBe(
      'cream'
    );
  });

  it('keeps a null freshness label null', () => {
    expect(mapOffer(apiOffer({ freshness: null }))?.freshness).toBeNull();
  });

  it.each([
    ['its own slug', { slug: 'Bad Slug' }, 'slug'],
    ['its category slug', { category: { slug: 'a/b', name: 'A' } }, 'category.slug'],
    [
      'its merchant slug',
      { merchant: { slug: '../x', name: 'X', logo_url: null } },
      'merchant.slug',
    ],
  ])('drops an offer with an unsafe %s and logs it', (_name, patch, field) => {
    expect(mapOffer(apiOffer(patch as Partial<ApiOffer>))).toBeNull();

    expect(mocks.warn).toHaveBeenCalledWith(
      'Catalogue: dropped an API record',
      expect.objectContaining({ kind: 'offer', field })
    );
  });

  it('quotes and shortens the dropped value so it cannot forge a log line', () => {
    mapOffer(apiOffer({ slug: `evil\nlevel=error ${'x'.repeat(200)}` }));

    const meta = mocks.warn.mock.calls[0][1] as { value: string };
    expect(meta.value).not.toContain('\n');
    expect(meta.value.length).toBeLessThanOrEqual(60);
  });

  it.each([
    ['an unreadable start date', { start_at: 'soon' }, 'start_at'],
    ['an unreadable end date', { end_at: 'never' }, 'end_at'],
    ['no verification date', { last_verified_at: null }, 'last_verified_at'],
    ['an unreadable verification date', { last_verified_at: 'yesterday' }, 'last_verified_at'],
    ['an unknown offer type', { offer_type: 'raffle' as never }, 'offer_type'],
  ])('drops an offer with %s rather than inventing a value', (_name, patch, field) => {
    expect(mapOffer(apiOffer(patch as Partial<ApiOffer>))).toBeNull();

    expect(mocks.warn).toHaveBeenCalledWith(
      'Catalogue: dropped an API record',
      expect.objectContaining({ field })
    );
  });
});

describe('merchant logos', () => {
  const withLogo = (logo_url: string | null) =>
    mapOffer(apiOffer({ merchant: { slug: 'tea-time', name: 'Tea Time', logo_url } }));

  it('keeps a plain https logo', () => {
    expect(withLogo('https://cdn.example/l.png')?.merchant?.logoUrl).toBe(
      'https://cdn.example/l.png'
    );
  });

  it.each([
    'javascript:alert(1)',
    'http://cdn.example/l.png',
    'data:image/png;base64,AAAA',
    'https://user:pass@cdn.example/l.png',
    '//cdn.example/l.png',
    'not a url',
    '',
  ])('drops %j to no logo', (logo) => {
    expect(withLogo(logo)?.merchant?.logoUrl).toBeNull();
  });

  it('keeps a missing logo null', () => {
    expect(withLogo(null)?.merchant?.logoUrl).toBeNull();
  });
});

describe('malformed API records', () => {
  const broken = (patch: Record<string, unknown>) => mapOffer(apiOffer(patch as Partial<ApiOffer>));

  it.each([
    ['a null category', { category: null }],
    ['a null merchant', { merchant: null }],
    ['no image', { image: undefined }],
    ['a string postage', { postage: 'free' }],
    ['no description list', { description: undefined }],
    ['a description that is not text', { description: [1, 2] }],
    ['no steps list', { steps: null }],
    ['a step without a title', { steps: [{ detail: 'x' }] }],
    ['a numeric title', { title: 42 }],
    ['numeric subscription terms', { subscription_terms: 5 }],
    ['object subscription terms', { subscription_terms: { text: 'x' } }],
    ['a freshness label that is not known', { freshness: 'trending' }],
    ['a numeric freshness label', { freshness: 3 }],
    ['a missing summary', { summary: undefined }],
    ['a non-boolean flag', { featured: 'yes' }],
    ['a category without a name', { category: { slug: 'food-drink' } }],
  ])('drops an offer with %s and logs it instead of throwing', (_name, patch) => {
    expect(() => broken(patch)).not.toThrow();
    expect(broken(patch)).toBeNull();
    expect(mocks.warn).toHaveBeenCalledWith(
      'Catalogue: dropped an API record',
      expect.objectContaining({ kind: 'offer' })
    );
  });

  it('drops a record that is not an object', () => {
    expect(mapOffer(null as unknown as ApiOffer)).toBeNull();
    expect(mapOffer('x' as unknown as ApiOffer)).toBeNull();
    expect(mapOffers([null as unknown as ApiOffer, apiOffer()])).toHaveLength(1);
  });

  it('drops malformed categories and ended stubs', () => {
    expect(mapCategory(null as unknown as ApiCategory)).toBeNull();
    expect(mapCategory({ ...apiCategory(), name: undefined } as unknown as ApiCategory)).toBeNull();
    expect(mapCategory({ ...apiCategory(), sort_order: '2' } as unknown as ApiCategory)).toBeNull();
    expect(
      mapEndedOffer({ slug: 'x', title: 'X', category: null } as unknown as ApiEndedOffer)
    ).toBeNull();
    expect(mapEndedOffer(null as unknown as ApiEndedOffer)).toBeNull();
    expect(
      mapEndedOffer({ slug: 'x', title: 'X', category: { slug: 'a' } } as unknown as ApiEndedOffer)
    ).toBeNull();
  });
});

describe('mapOffers', () => {
  it('keeps the good offers and drops the bad ones', () => {
    const offers = mapOffers([
      apiOffer({ id: 'a', slug: 'good-one' }),
      apiOffer({ id: 'b', slug: 'BAD' }),
      apiOffer({ id: 'c', slug: 'good-two' }),
    ]);

    expect(offers.map((offer) => offer.slug)).toEqual(['good-one', 'good-two']);
  });
});

describe('mapCategory', () => {
  it('maps a category and marks it enabled', () => {
    expect(mapCategory(apiCategory())).toEqual({
      slug: 'food-drink',
      name: 'Food & drink',
      shortName: 'Food',
      description: 'Tasty things',
      intro: 'A longer intro',
      tint: 'sage',
      sortOrder: 2,
      enabled: true,
    });
  });

  it('falls back to a neutral tint and drops an unsafe slug', () => {
    expect(mapCategory(apiCategory({ tint: 'neon' }))?.tint).toBe('cream');
    expect(mapCategory(apiCategory({ slug: 'Food & Drink' }))).toBeNull();
    expect(mocks.warn).toHaveBeenCalledTimes(1);
  });
});

describe('mapEndedOffer', () => {
  const stub = (patch: Partial<ApiEndedOffer> = {}): ApiEndedOffer => ({
    slug: 'old-tea',
    title: 'Old tea',
    category: { slug: 'food-drink', name: 'Food & drink' },
    ended_at: '2026-05-31T23:59:00Z',
    ...patch,
  });

  it('maps the 410 stub with a London end date', () => {
    expect(mapEndedOffer(stub())).toEqual({
      slug: 'old-tea',
      title: 'Old tea',
      categorySlug: 'food-drink',
      categoryName: 'Food & drink',
      endedAt: '2026-06-01', // 23:59 UTC on 31 May is 00:59 on 1 June in BST
    });
  });

  it('keeps an unknown end date null', () => {
    expect(mapEndedOffer(stub({ ended_at: null }))?.endedAt).toBeNull();
  });

  it('drops a stub with an unsafe slug', () => {
    expect(mapEndedOffer(stub({ slug: 'No Good' }))).toBeNull();
    expect(mapEndedOffer(stub({ category: { slug: 'a b', name: 'A' } }))).toBeNull();
    expect(mocks.warn).toHaveBeenCalledTimes(2);
  });
});
