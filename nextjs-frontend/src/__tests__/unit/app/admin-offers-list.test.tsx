/**
 * The admin offers list: URL parameters, the stats strip (counts come only from the API), the status chips, the
 * filters, and the table with its status pills and verified dates. Server components are rendered with
 * `render(await Component(...))` and asserted through roles, names and text, not markup strings.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';

const mocks = vi.hoisted(() => ({
  fetchAdminOffers: vi.fn(),
  fetchOfferStatusCounts: vi.fn(),
  fetchAdminCategories: vi.fn(),
  fetchAdminMerchants: vi.fn(),
  requireAdminSession: vi.fn(),
  logError: vi.fn(),
}));

vi.mock('@/lib/api/admin-guard', () => ({ requireAdminSession: mocks.requireAdminSession }));
vi.mock('@/lib/api/admin-offers', async () => {
  const actual =
    await vi.importActual<typeof import('@/lib/api/admin-offers')>('@/lib/api/admin-offers');
  return {
    ...actual,
    fetchAdminOffers: mocks.fetchAdminOffers,
    fetchOfferStatusCounts: mocks.fetchOfferStatusCounts,
  };
});
vi.mock('@/lib/api/admin-categories', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api/admin-categories')>(
    '@/lib/api/admin-categories'
  );
  return { ...actual, fetchAdminCategories: mocks.fetchAdminCategories };
});
vi.mock('@/lib/api/admin-merchants', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api/admin-merchants')>(
    '@/lib/api/admin-merchants'
  );
  return { ...actual, fetchAdminMerchants: mocks.fetchAdminMerchants };
});
vi.mock('@/lib/logger', () => ({
  logger: { error: mocks.logError, warn: vi.fn(), info: vi.fn() },
}));
vi.mock('react', async () => {
  const actual = await vi.importActual<typeof import('react')>('react');
  // `cache` memoises per request in Next; in a unit test each call must hit the mocks again.
  return { ...actual, cache: <T,>(fn: T) => fn };
});
vi.mock('@/app/(admin)/admin/offers/_components/offer-row-actions', () => ({
  OfferRowActions: ({
    offerId,
    status,
    reviewToken,
  }: {
    offerId: string;
    status: string;
    reviewToken: string;
  }) => (
    <button
      type="button"
      data-row-actions={offerId}
      data-status={status}
      data-review-token={reviewToken}
    >
      Row actions
    </button>
  ),
}));

import AdminOffersPage from '@/app/(admin)/admin/offers/page';
import { OfferStatsStrip } from '@/app/(admin)/admin/offers/offers-stats';
import { OffersResults } from '@/app/(admin)/admin/offers/offers-results';
import { offersHref, parseOffersQuery } from '@/app/(admin)/admin/offers/offers-query';

const SECRET = 'SECRET postgres://user:pw@host/db';

const offer = (overrides: Record<string, unknown> = {}) => ({
  id: 'o1',
  title: 'Free coffee sample',
  slug: 'free-coffee-sample',
  category_id: 'c1',
  merchant_id: 'm1',
  offer_type: 'FREE_SAMPLE',
  status: 'PUBLISHED',
  featured: false,
  end_at: null,
  last_verified_at: '2026-03-05T10:00:00Z',
  updated_at: '2026-03-06T10:00:00Z',
  review_token: 'token-of-the-list-read',
  ...overrides,
});

function listOk(offers: unknown[], total = offers.length) {
  mocks.fetchAdminOffers.mockResolvedValue({
    success: true,
    data: { offers, total_count: total, limit: 25, offset: 0 },
  });
}

function pickLists(totalCategories = 2, totalMerchants = 1) {
  mocks.fetchAdminCategories.mockResolvedValue({
    success: true,
    data: {
      categories: [
        { id: 'c1', name: 'Food & drink', enabled: true },
        { id: 'c2', name: 'Beauty', enabled: false },
      ],
      total_count: totalCategories,
      limit: 100,
      offset: 0,
    },
  });
  mocks.fetchAdminMerchants.mockResolvedValue({
    success: true,
    data: {
      merchants: [{ id: 'm1', name: 'Good Beans', active: true }],
      total_count: totalMerchants,
      limit: 100,
      offset: 0,
    },
  });
}

const renderResults = async (query: Parameters<typeof OffersResults>[0]['query']) =>
  render(await OffersResults({ query }));

beforeEach(() => {
  vi.clearAllMocks();
  mocks.requireAdminSession.mockResolvedValue({ ok: true });
  mocks.fetchOfferStatusCounts.mockResolvedValue({
    success: true,
    data: { TOTAL: 0, PUBLISHED: 0, DRAFT: 0, EXPIRED: 0, ARCHIVED: 0 },
  });
  listOk([]);
  pickLists();
});

describe('offers URL parameters', () => {
  it('parses and bounds every parameter', () => {
    expect(
      parseOffersQuery({
        q: `  ${'x'.repeat(150)} `,
        status: 'Published',
        category: 'c_1',
        page: '3',
      })
    ).toEqual({ q: 'x'.repeat(100), status: 'PUBLISHED', category: 'c_1', page: 3 });
  });

  it('ignores unknown statuses, bad category ids and bad pages', () => {
    expect(parseOffersQuery({ q: '  ', status: 'deleted', category: '../x', page: '-2' })).toEqual({
      q: undefined,
      status: undefined,
      category: undefined,
      page: 1,
    });
    expect(parseOffersQuery({ page: 'abc' }).page).toBe(1);
  });

  it('clamps a huge page to the last one the API can serve', () => {
    expect(parseOffersQuery({ page: '99999999' }).page).toBe(40_001);
  });

  it('builds links that keep the filters and drop defaults', () => {
    expect(offersHref({})).toBe('/admin/offers');
    expect(offersHref({ q: 'a b', status: 'DRAFT', category: 'c1', page: 2 })).toBe(
      '/admin/offers?q=a+b&status=draft&category=c1&page=2'
    );
    expect(offersHref({ page: 1 })).toBe('/admin/offers');
  });
});

describe('OfferStatsStrip', () => {
  it('shows the counts the API returned, each under its label', async () => {
    mocks.fetchOfferStatusCounts.mockResolvedValue({
      success: true,
      data: { TOTAL: 31, PUBLISHED: 17, DRAFT: 8, EXPIRED: 4, ARCHIVED: 2 },
    });
    render(await OfferStatsStrip());

    for (const [label, count] of [
      ['Total offers', '31'],
      ['Published', '17'],
      ['Drafts', '8'],
      ['Expired', '4'],
      ['Archived', '2'],
    ]) {
      expect(screen.getByText(label).parentElement).toHaveTextContent(count);
    }
    expect(document.querySelector('dl')).toHaveAttribute('aria-label', 'Offer counts');
  });

  it('shows no numbers at all when a count cannot be read, and a way to retry', async () => {
    mocks.fetchOfferStatusCounts.mockResolvedValue({ success: false, error: SECRET, status: 500 });
    render(await OfferStatsStrip());

    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(screen.queryByText('Total offers')).not.toBeInTheDocument();
    expect(screen.queryByText(/SECRET/)).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Retry' })).toHaveAttribute('href', '/admin/offers');
  });

  it('says a rate limit is a rate limit, and does not log it as an error', async () => {
    mocks.fetchOfferStatusCounts.mockResolvedValue({ success: false, error: SECRET, status: 429 });
    render(await OfferStatsStrip());
    expect(screen.getByRole('alert')).toHaveTextContent('Too many requests');
    expect(mocks.logError).not.toHaveBeenCalled();
  });
});

describe('admin offers page shell', () => {
  // The results and the stats stream in behind Suspense; the shell is checked around them.
  async function renderPage(params: Record<string, string> = {}) {
    const element = await AdminOffersPage({ searchParams: Promise.resolve(params) });
    return render(element);
  }

  it('has the heading, an add link and a labelled GET search with its filters kept', async () => {
    await renderPage({ q: 'coffee', status: 'draft', category: 'c1' });

    expect(screen.getByRole('heading', { level: 1, name: 'Freebies' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Add freebie' })).toHaveAttribute(
      'href',
      '/admin/offers/new'
    );
    const search = screen.getByRole('search');
    expect(search).toHaveAttribute('action', '/admin/offers');
    expect(search).toHaveAttribute('method', 'get');
    expect(within(search).getByLabelText('Search offers by title, slug or summary')).toHaveValue(
      'coffee'
    );
    expect(within(search).getByLabelText('Category')).toHaveValue('c1');
    expect(within(search).getByRole('option', { name: 'Beauty (disabled)' })).toBeInTheDocument();
    expect(search.querySelector('input[type="hidden"][name="status"]')).toHaveValue('draft');
  });

  it('renders the status chips as links in the design order, marking only the current one', async () => {
    await renderPage({ status: 'expired', q: 'x' });

    const nav = screen.getByRole('navigation', { name: 'Filter by status' });
    expect(
      within(nav)
        .getAllByRole('link')
        .map((link) => link.textContent)
    ).toEqual(['All', 'Published', 'Draft', 'Expired', 'Archived']);
    const current = within(nav)
      .getAllByRole('link')
      .filter((link) => link.getAttribute('aria-current') === 'true');
    expect(current.map((link) => link.textContent)).toEqual(['Expired']);
    expect(within(nav).getByRole('link', { name: 'Draft' })).toHaveAttribute(
      'href',
      '/admin/offers?q=x&status=draft'
    );
  });

  it('keeps the page and says so when the category filter cannot load', async () => {
    mocks.fetchAdminCategories.mockResolvedValue({ success: false, error: SECRET, status: 500 });
    await renderPage();

    expect(screen.getByRole('alert')).toHaveTextContent('The category filter could not be loaded.');
    expect(screen.queryByText(/SECRET/)).not.toBeInTheDocument();
  });

  it('tells the admin when the category filter lists only the first 100', async () => {
    pickLists(150);
    await renderPage();
    expect(
      screen.getByText('The category filter lists only the first 100 categories.')
    ).toBeInTheDocument();
  });
});

describe('OffersResults', () => {
  it('renders a named table with names, status pills and verified dates', async () => {
    listOk([
      offer(),
      offer({ id: 'o2', title: 'Draft one', status: 'DRAFT', last_verified_at: null }),
      offer({ id: 'o3', status: 'EXPIRED', category_id: 'c2', title: 'Old one' }),
      offer({ id: 'o4', status: 'ARCHIVED', category_id: 'zzz', title: 'Gone one' }),
      offer({ id: 'o5', status: 'SOMETHING_NEW', title: 'Newer API one' }),
    ]);
    await renderResults(parseOffersQuery({}));

    const table = screen.getByRole('table', { name: 'All offers (5)' });
    expect(
      within(table)
        .getAllByRole('columnheader')
        .map((header) => header.textContent)
    ).toEqual(['Offer', 'Category', 'Status', 'Verified', 'Actions']);

    const row = (title: string) =>
      within(table)
        .getAllByRole('row')
        .find((candidate) => within(candidate).queryByRole('link', { name: title }))!;

    const published = row('Free coffee sample');
    expect(within(published).getByRole('link', { name: 'Free coffee sample' })).toHaveAttribute(
      'href',
      '/admin/offers/o1'
    );
    expect(published).toHaveTextContent('Good Beans · Free sample');
    expect(within(published).getByText('Food & drink')).toBeInTheDocument();
    expect(within(published).getByTestId('offer-status')).toHaveTextContent('Published');
    expect(within(published).getByText('5 Mar 2026')).toBeInTheDocument();

    expect(within(row('Draft one')).getByTestId('offer-status')).toHaveTextContent('Draft');
    expect(within(row('Draft one')).getByText('Not verified')).toBeInTheDocument();
    expect(within(row('Old one')).getByTestId('offer-status')).toHaveTextContent('Expired');
    expect(within(row('Old one')).getByText('Beauty (disabled)')).toBeInTheDocument();
    expect(within(row('Gone one')).getByTestId('offer-status')).toHaveTextContent('Archived');
    expect(within(row('Gone one')).getByText('Unknown')).toBeInTheDocument();

    // A status the UI does not know is a neutral "Unknown" pill with no row actions.
    expect(within(row('Newer API one')).getByTestId('offer-status')).toHaveTextContent('Unknown');
    expect(within(row('Newer API one')).queryByRole('button')).not.toBeInTheDocument();
    expect(within(published).getByRole('button', { name: 'Row actions' })).toHaveAttribute(
      'data-status',
      'PUBLISHED'
    );
    // The row menu publishes and verifies with the token this list read carried.
    expect(within(published).getByRole('button', { name: 'Row actions' })).toHaveAttribute(
      'data-review-token',
      'token-of-the-list-read'
    );
    // The title link is the one way into the editor (the menu has Edit too); no second text link.
    expect(within(published).getAllByRole('link')).toHaveLength(1);
    expect(screen.getByText('Showing 1 to 5 of 5 offers')).toBeInTheDocument();
  });

  it('gives the draft pill its own pencil icon', async () => {
    listOk([offer({ status: 'DRAFT' }), offer({ id: 'o2', title: 'Live one' })]);
    await renderResults(parseOffersQuery({}));
    const [draftPill, livePill] = screen.getAllByTestId('offer-status');
    expect(draftPill.querySelector('svg.lucide-pencil')).not.toBeNull();
    expect(livePill.querySelector('svg.lucide-pencil')).toBeNull();
  });

  it('asks the API for the page, filters and search text', async () => {
    listOk([offer()], 60);
    await renderResults(
      parseOffersQuery({ q: 'tea', status: 'published', category: 'c1', page: '2' })
    );
    expect(mocks.fetchAdminOffers).toHaveBeenCalledWith({
      search: 'tea',
      status: 'PUBLISHED',
      categoryId: 'c1',
      limit: 25,
      offset: 25,
    });
  });

  it('links to the neighbouring pages, keeping the filters, and says which page this is', async () => {
    listOk([offer()], 80);
    await renderResults(parseOffersQuery({ q: 'tea', status: 'draft', page: '2' }));

    const pagination = screen.getByRole('navigation', { name: 'Pagination' });
    expect(within(pagination).getByRole('link', { name: 'Previous' })).toHaveAttribute(
      'href',
      '/admin/offers?q=tea&status=draft'
    );
    expect(within(pagination).getByRole('link', { name: 'Next' })).toHaveAttribute(
      'href',
      '/admin/offers?q=tea&status=draft&page=3'
    );
    expect(within(pagination).getByText('Page 2 of 4')).toBeInTheDocument();
    expect(screen.getByText('Showing 26 to 26 of 80 offers')).toBeInTheDocument();
  });

  it('says what an empty result means and offers a way out', async () => {
    listOk([]);
    const filtered = await renderResults(parseOffersQuery({ q: '<b>x</b>' }));
    expect(screen.getByText("No offers match '<b>x</b>'")).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Clear filters' })).toHaveAttribute(
      'href',
      '/admin/offers'
    );
    filtered.unmount();

    const none = await renderResults(parseOffersQuery({}));
    expect(screen.getByText('No offers yet')).toBeInTheDocument();
    none.unmount();

    await renderResults(parseOffersQuery({ page: '9' }));
    expect(screen.getByRole('link', { name: 'Back to first page' })).toBeInTheDocument();
  });

  it('shows a curated error with Retry and never the backend text', async () => {
    mocks.fetchAdminOffers.mockResolvedValue({ success: false, error: SECRET, status: 403 });
    await renderResults(parseOffersQuery({ q: 'tea' }));

    expect(screen.getByRole('alert')).toHaveTextContent('You do not have permission to do that');
    expect(screen.getByRole('link', { name: 'Retry' })).toHaveAttribute(
      'href',
      '/admin/offers?q=tea'
    );
    expect(screen.queryByText(/SECRET/)).not.toBeInTheDocument();
  });

  it('logs unexpected failures without the backend text', async () => {
    mocks.fetchAdminOffers.mockResolvedValue({ success: false, error: SECRET, status: 500 });
    await renderResults(parseOffersQuery({}));
    expect(mocks.logError).toHaveBeenCalledTimes(1);
    expect(JSON.stringify(mocks.logError.mock.calls)).not.toContain('SECRET');
  });

  it('names a category or merchant past the first 100 instead of calling it unknown', async () => {
    pickLists(150, 120);
    listOk([offer({ category_id: 'c-late', merchant_id: 'm-late' })]);
    await renderResults(parseOffersQuery({}));

    const row = screen.getAllByRole('row')[1];
    expect(within(row).getByText('Not in the first 100')).toBeInTheDocument();
    expect(row).toHaveTextContent('Not in the first 100 · Free sample');
    expect(screen.queryByText('Unknown')).not.toBeInTheDocument();
  });
});
