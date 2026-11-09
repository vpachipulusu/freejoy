// @vitest-environment node
/**
 * The admin catalogue wrappers call the /v1/admin/{offers,categories,merchants} routes with the session cookie
 * and the internal key, build the right request bodies, and expose camelCase views.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const cookieGet = vi.fn();
vi.mock('next/headers', () => ({
  cookies: vi.fn(async () => ({ get: cookieGet })),
  headers: vi.fn(async () => ({ get: vi.fn() })),
}));

import { emptyOfferFormValues, type OfferFormValues } from '@/lib/admin-catalogue/offer-schema';
import {
  emptyCategoryFormValues,
  type CategoryFormValues,
} from '@/lib/admin-catalogue/category-schema';
import {
  emptyMerchantFormValues,
  type MerchantFormValues,
} from '@/lib/admin-catalogue/merchant-schema';
import {
  fetchAdminOffer,
  fetchAdminOffers,
  fetchOfferStatusCounts,
  runAdminOfferAction,
  toOfferDetail,
  toOfferRow,
} from '@/lib/api/admin-offers';
import { toCreateOfferBody, toUpdateOfferBody } from '@/lib/api/admin-offers-body';
import {
  setAdminCategoryEnabled,
  toCategoryView,
  toCreateCategoryBody,
  toUpdateCategoryBody,
} from '@/lib/api/admin-categories';
import {
  setAdminMerchantActive,
  toCreateMerchantBody,
  toMerchantView,
  toUpdateMerchantBody,
} from '@/lib/api/admin-merchants';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const OFFER_SUMMARY = {
  id: 'o1',
  title: 'Free coffee',
  slug: 'free-coffee',
  category_id: 'c1',
  merchant_id: 'm1',
  offer_type: 'FREE_SAMPLE',
  status: 'PUBLISHED',
  featured: false,
  end_at: null,
  last_verified_at: '2026-01-02T00:00:00Z',
  updated_at: '2026-01-03T00:00:00Z',
  review_token: 'a1b2c3-review-token',
};

const OFFER_OUT = {
  ...OFFER_SUMMARY,
  summary: 'A sachet.',
  description: ['One'],
  image_tint: 'mint',
  image_label: 'coffee',
  image_alt: 'A sachet',
  source_url: 'https://example.com/x',
  postage_kind: 'FREE',
  postage_amount_minor: null,
  postage_currency: null,
  requires_card: false,
  subscription_terms: null,
  eligibility: 'UK',
  availability: 'Now',
  steps: [{ title: 'Go', detail: 'Click' }, { nope: 1 }],
  start_at: null,
  freshness: null,
  publisher_notes: null,
  created_at: '2026-01-01T00:00:00Z',
  links: [
    {
      id: 'l1',
      offer_id: 'o1',
      provider: 'awin',
      destination_url: 'https://example.com/go',
      tracking_id: 'secret-track',
      active: true,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    },
  ],
};

function offerForm(overrides: Partial<OfferFormValues> = {}): OfferFormValues {
  return {
    ...emptyOfferFormValues(),
    title: 'Free coffee',
    summary: 'A sachet.',
    description: 'One.\n\nTwo.',
    categoryId: 'c1',
    merchantId: 'm1',
    imageLabel: 'coffee',
    imageAlt: 'A sachet',
    eligibility: 'UK',
    availability: 'Now',
    ...overrides,
  };
}

describe('admin catalogue wrappers', () => {
  const fetchMock = vi.fn<(request: Request) => Promise<Response>>();

  beforeEach(() => {
    vi.stubEnv('BACKEND_API_URL', 'http://backend.test');
    vi.stubEnv('BACKEND_INTERNAL_KEY', 'server-key');
    cookieGet.mockReturnValue({ name: 'session', value: 'firebase-session-cookie' });
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    fetchMock.mockReset();
    cookieGet.mockReset();
  });

  describe('offers', () => {
    it('lists with filters, trimming and capping the search text', async () => {
      fetchMock.mockResolvedValue(
        jsonResponse({ success: true, data: { offers: [], total_count: 0, limit: 25, offset: 50 } })
      );

      const result = await fetchAdminOffers({
        search: `  ${'x'.repeat(150)}  `,
        status: 'DRAFT',
        categoryId: 'c1',
        offset: 50,
      });

      const request = fetchMock.mock.calls[0][0];
      const url = new URL(request.url);
      expect(url.pathname).toBe('/v1/admin/offers');
      expect(url.searchParams.get('limit')).toBe('25');
      expect(url.searchParams.get('offset')).toBe('50');
      expect(url.searchParams.get('status')).toBe('DRAFT');
      expect(url.searchParams.get('category_id')).toBe('c1');
      expect(url.searchParams.get('search')).toBe('x'.repeat(100));
      expect(request.headers.get('X-Session-Token')).toBe('firebase-session-cookie');
      expect(request.headers.get('X-Internal-Key')).toBe('server-key');
      expect(result.success).toBe(true);
    });

    it('omits empty filters', async () => {
      fetchMock.mockResolvedValue(
        jsonResponse({ success: true, data: { offers: [], total_count: 0, limit: 25, offset: 0 } })
      );
      await fetchAdminOffers({ search: '   ' });
      const url = new URL(fetchMock.mock.calls[0][0].url);
      expect(url.searchParams.has('search')).toBe(false);
      expect(url.searchParams.has('status')).toBe(false);
      expect(url.searchParams.has('category_id')).toBe(false);
    });

    it('reads one offer by id', async () => {
      fetchMock.mockResolvedValue(jsonResponse({ success: true, data: OFFER_OUT }));
      const result = await fetchAdminOffer('o1');
      expect(new URL(fetchMock.mock.calls[0][0].url).pathname).toBe('/v1/admin/offers/o1');
      expect(result.success && result.data.id).toBe('o1');
    });

    it.each([
      [{ action: 'publish', reviewToken: 'tok-1' }, '/v1/admin/offers/o1/publish'],
      [{ action: 'unpublish' }, '/v1/admin/offers/o1/unpublish'],
      [{ action: 'expire' }, '/v1/admin/offers/o1/expire'],
      [{ action: 'archive' }, '/v1/admin/offers/o1/archive'],
      [{ action: 'verify', reviewToken: 'tok-1' }, '/v1/admin/offers/o1/verify'],
    ] as const)('posts %j to its own route', async (request, path) => {
      fetchMock.mockResolvedValue(jsonResponse({ success: true, data: OFFER_OUT }));
      await runAdminOfferAction('o1', request);
      const sent = fetchMock.mock.calls[0][0];
      expect(sent.method).toBe('POST');
      expect(new URL(sent.url).pathname).toBe(path);
    });

    it.each(['publish', 'verify'] as const)(
      'sends the review token the admin loaded with %s (and no note: the UI does not use it)',
      async (action) => {
        fetchMock.mockResolvedValue(jsonResponse({ success: true, data: OFFER_OUT }));
        await runAdminOfferAction('o1', { action, reviewToken: 'tok-1' });
        expect(await fetchMock.mock.calls[0][0].json()).toEqual({ review_token: 'tok-1' });
      }
    );

    it.each(['unpublish', 'expire', 'archive'] as const)(
      'sends no body with %s: taking an offer off the site needs no review token',
      async (action) => {
        fetchMock.mockResolvedValue(jsonResponse({ success: true, data: OFFER_OUT }));
        await runAdminOfferAction('o1', { action });
        expect(await fetchMock.mock.calls[0][0].text()).toBe('');
      }
    );

    it('hands the 409 offer_changed code back so the action can show its curated message', async () => {
      fetchMock.mockResolvedValue(
        jsonResponse({ success: false, error: 'backend words', code: 'offer_changed' }, 409)
      );
      const result = await runAdminOfferAction('o1', { action: 'publish', reviewToken: 'old' });
      expect(result).toMatchObject({ success: false, status: 409, code: 'offer_changed' });
    });

    it('counts offers per status from the list totals, summing the total, without inventing numbers', async () => {
      const totals: Record<string, number> = {
        DRAFT: 3,
        PUBLISHED: 4,
        EXPIRED: 2,
        ARCHIVED: 1,
      };
      fetchMock.mockImplementation(async (request) => {
        const status = new URL(request.url).searchParams.get('status') ?? '';
        expect(status).not.toBe('');
        return jsonResponse({
          success: true,
          data: { offers: [], total_count: totals[status], limit: 1, offset: 0 },
        });
      });

      const result = await fetchOfferStatusCounts();

      expect(result).toEqual({
        success: true,
        data: { TOTAL: 10, DRAFT: 3, PUBLISHED: 4, EXPIRED: 2, ARCHIVED: 1 },
      });
      expect(fetchMock).toHaveBeenCalledTimes(4);
    });

    it('fails the whole count read when one call fails', async () => {
      fetchMock.mockImplementation(async (request) =>
        new URL(request.url).searchParams.get('status') === 'EXPIRED'
          ? jsonResponse({ success: false, error: 'x' }, 500)
          : jsonResponse({
              success: true,
              data: { offers: [], total_count: 1, limit: 1, offset: 0 },
            })
      );
      const result = await fetchOfferStatusCounts();
      expect(result.success).toBe(false);
    });

    it('maps rows and details to camelCase views, without tracking ids', () => {
      expect(toOfferRow({ ...OFFER_SUMMARY, status: 'PUBLISHED' } as never)).toMatchObject({
        id: 'o1',
        categoryId: 'c1',
        lastVerifiedAt: '2026-01-02T00:00:00Z',
        status: 'PUBLISHED',
        reviewToken: 'a1b2c3-review-token',
      });
      const detail = toOfferDetail(OFFER_OUT as never);
      expect(detail.reviewToken).toBe('a1b2c3-review-token');
      expect(detail.steps).toEqual([
        { title: 'Go', detail: 'Click' },
        { title: '', detail: '' },
      ]);
      expect(detail.links).toEqual([
        { id: 'l1', provider: 'awin', destinationUrl: 'https://example.com/go', active: true },
      ]);
      expect(JSON.stringify(detail)).not.toContain('secret-track');
    });

    it('keeps an unknown status as UNKNOWN instead of guessing a draft', () => {
      expect(toOfferRow({ ...OFFER_SUMMARY, status: 'SOMETHING_NEW' } as never).status).toBe(
        'UNKNOWN'
      );
    });
  });

  describe('offer request bodies', () => {
    it('builds a create body in snake_case with nulls for blank optional fields', () => {
      const body = toCreateOfferBody(offerForm());
      expect(body).toMatchObject({
        title: 'Free coffee',
        description: ['One.', 'Two.'],
        category_id: 'c1',
        merchant_id: 'm1',
        offer_type: 'FREE_SAMPLE',
        postage_kind: 'FREE',
        postage_amount_minor: null,
        postage_currency: null,
        source_url: null,
        start_at: null,
        end_at: null,
        freshness: null,
        publisher_notes: null,
        subscription_terms: null,
        steps: [],
      });
      expect('slug' in body).toBe(false);
    });

    it('sends the slug on create only when typed, and never on update', () => {
      const values = offerForm({ slug: 'my-slug' });
      expect(toCreateOfferBody(values).slug).toBe('my-slug');
      expect('slug' in toUpdateOfferBody(values)).toBe(false);
    });

    it('prices postage in minor units and GBP, and clears it for other kinds', () => {
      expect(
        toCreateOfferBody(offerForm({ postageKind: 'COST', postageAmount: '2.99' }))
      ).toMatchObject({
        postage_kind: 'COST',
        postage_amount_minor: 299,
        postage_currency: 'GBP',
      });
      expect(
        toUpdateOfferBody(offerForm({ postageKind: 'CHECK_TERMS', postageAmount: '2.99' }))
      ).toMatchObject({
        postage_amount_minor: null,
        postage_currency: null,
      });
    });

    it('turns local date-times into UTC timestamps and trims steps to title and detail', () => {
      const body = toUpdateOfferBody(
        offerForm({
          startAt: '2026-03-01T09:30',
          endAt: '2026-03-02T10:00',
          freshness: 'POPULAR',
          sourceUrl: 'https://example.com/x',
          steps: [{ title: 'Go', detail: 'Click' }],
        })
      );
      expect(body).toMatchObject({
        start_at: '2026-03-01T09:30:00Z',
        end_at: '2026-03-02T10:00:00Z',
        freshness: 'POPULAR',
        source_url: 'https://example.com/x',
        steps: [{ title: 'Go', detail: 'Click' }],
      });
    });
  });

  describe('categories', () => {
    const values: CategoryFormValues = {
      ...emptyCategoryFormValues(),
      name: 'Beauty',
      shortName: 'Beauty',
      description: 'Skincare.',
      intro: 'Try it.',
      sortOrder: '3',
    };

    it('builds create and update bodies', () => {
      expect(toCreateCategoryBody({ ...values, slug: 'beauty' })).toEqual({
        name: 'Beauty',
        slug: 'beauty',
        short_name: 'Beauty',
        description: 'Skincare.',
        intro: 'Try it.',
        tint: 'blush',
        sort_order: 3,
        enabled: true,
      });
      expect('slug' in toCreateCategoryBody(values)).toBe(false);
      const update = toUpdateCategoryBody({ ...values, slug: 'ignored', enabled: false });
      expect(update).toEqual({
        name: 'Beauty',
        short_name: 'Beauty',
        description: 'Skincare.',
        intro: 'Try it.',
        tint: 'blush',
        sort_order: 3,
      });
    });

    it('enables and disables through their own routes', async () => {
      fetchMock.mockResolvedValue(jsonResponse({ success: true, data: {} }));
      await setAdminCategoryEnabled('c1', true);
      await setAdminCategoryEnabled('c1', false);
      expect(new URL(fetchMock.mock.calls[0][0].url).pathname).toBe(
        '/v1/admin/categories/c1/enable'
      );
      expect(new URL(fetchMock.mock.calls[1][0].url).pathname).toBe(
        '/v1/admin/categories/c1/disable'
      );
    });

    it('maps a category to a view', () => {
      expect(
        toCategoryView({
          id: 'c1',
          name: 'Beauty',
          slug: 'beauty',
          short_name: 'B',
          description: 'd',
          intro: 'i',
          tint: 'blush',
          sort_order: 1,
          enabled: false,
          created_at: 'x',
          updated_at: 'y',
        })
      ).toMatchObject({ id: 'c1', shortName: 'B', sortOrder: 1, enabled: false, updatedAt: 'y' });
    });
  });

  describe('merchants', () => {
    const values: MerchantFormValues = { ...emptyMerchantFormValues(), name: 'Acme' };

    it('builds create and update bodies, clearing blank URLs with null', () => {
      expect(toCreateMerchantBody(values)).toEqual({
        name: 'Acme',
        website: null,
        logo_url: null,
        active: true,
      });
      expect(toCreateMerchantBody({ ...values, slug: 'acme' }).slug).toBe('acme');
      expect(
        toUpdateMerchantBody({ ...values, website: 'https://acme.example', active: false })
      ).toEqual({ name: 'Acme', website: 'https://acme.example', logo_url: null });
    });

    it('activates and deactivates through their own routes', async () => {
      fetchMock.mockResolvedValue(jsonResponse({ success: true, data: {} }));
      await setAdminMerchantActive('m1', true);
      await setAdminMerchantActive('m1', false);
      expect(new URL(fetchMock.mock.calls[0][0].url).pathname).toBe(
        '/v1/admin/merchants/m1/activate'
      );
      expect(new URL(fetchMock.mock.calls[1][0].url).pathname).toBe(
        '/v1/admin/merchants/m1/deactivate'
      );
    });

    it('maps a merchant to a view', () => {
      expect(
        toMerchantView({
          id: 'm1',
          name: 'Acme',
          slug: 'acme',
          website: null,
          logo_url: null,
          active: true,
          created_at: 'x',
          updated_at: 'y',
        })
      ).toEqual({
        id: 'm1',
        name: 'Acme',
        slug: 'acme',
        website: null,
        logoUrl: null,
        active: true,
        updatedAt: 'y',
      });
    });
  });
});
