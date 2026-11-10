import type { ReactElement } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import type { EndedOffer, OfferLookup } from '@/lib/catalogue';
import { CatalogueUnavailableError } from '@/lib/catalogue/errors';
import type { Offer } from '@/lib/catalogue/types';

const mocks = vi.hoisted(() => ({
  notFound: vi.fn(),
  source: vi.fn(),
  lookup: vi.fn(),
  search: vi.fn(),
  featured: vi.fn(),
}));

vi.mock('next/navigation', () => ({
  notFound: () => {
    mocks.notFound();
    throw new Error('NEXT_NOT_FOUND');
  },
  usePathname: () => '/',
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn(), back: vi.fn() }),
}));

// Everything is the real facade (fixtures under test) except the source, the offer lookup, the search
// and the featured list, which a test can point at API-shaped answers or an outage. That proves the
// indexing flip is driven by the source and the data, with no code change.
vi.mock('@/lib/catalogue', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/catalogue')>();
  const override =
    <T extends (...args: never[]) => unknown>(mock: ReturnType<typeof vi.fn>, real: T) =>
    (...args: Parameters<T>) =>
      mock.getMockImplementation()
        ? (mock as (...a: unknown[]) => unknown)(...args)
        : real(...args);
  return {
    ...actual,
    getCatalogueSource: override(mocks.source, actual.getCatalogueSource),
    lookupOffer: override(mocks.lookup, actual.lookupOffer),
    searchOffers: override(mocks.search, actual.searchOffers),
    getFeaturedOffers: override(mocks.featured, actual.getFeaturedOffers),
  };
});

import CategoryPage, * as categoryRoute from '@/app/(public)/category/[slug]/page';
import OfferPage, * as offerRoute from '@/app/(public)/freebies/[slug]/page';
import * as freebiesRoute from '@/app/(public)/freebies/page';
import HomePage, * as homeRoute from '@/app/(public)/page';
import { generateMetadata as alertsGenerateMetadata } from '@/app/(public)/alerts/page';
import { HomeSections } from '@/components/home/home-sections';
import { JsonLd } from '@/components/seo/json-ld';
import { OFFERS } from '@/lib/catalogue/fixtures';

const homeMetadata = homeRoute.generateMetadata;
const freebiesMetadata = freebiesRoute.generateMetadata;
const categoryMetadata = categoryRoute.generateMetadata;
const offerMetadata = offerRoute.generateMetadata;

const FIXTURE_LIVE = OFFERS.find(
  (offer) => offer.status === 'published' && offer.endAt && offer.categorySlug === 'beauty'
) as Offer;
const FIXTURE_EXPIRED = OFFERS.find((offer) => offer.status === 'expired') as Offer;

const apiOffer = (patch: Partial<Offer> = {}): Offer => ({
  ...FIXTURE_LIVE,
  source: 'api',
  demo: false,
  endAt: '2027-01-31',
  ...patch,
});

const found = (offer: Offer): OfferLookup => ({ kind: 'found', offer });
const ended = (patch: Partial<EndedOffer> = {}): OfferLookup => ({
  kind: 'ended',
  ended: {
    slug: FIXTURE_LIVE.slug,
    title: FIXTURE_LIVE.title,
    categorySlug: 'beauty',
    categoryName: 'Beauty',
    endedAt: '2026-10-24',
    ...patch,
  },
});

const search = (params: Record<string, string>) => ({ searchParams: Promise.resolve(params) });
const offerParams = { params: Promise.resolve({ slug: FIXTURE_LIVE.slug }) };
const outage = () => Promise.reject(new CatalogueUnavailableError());

const jsonLdOf = (container: HTMLElement) =>
  Array.from(container.querySelectorAll('script[type="application/ld+json"]')).flatMap((script) => {
    const parsed = JSON.parse(script.textContent ?? 'null');
    return Array.isArray(parsed) ? parsed : [parsed];
  });

beforeEach(() => {
  mocks.notFound.mockClear();
  for (const mock of [mocks.source, mocks.lookup, mocks.search, mocks.featured]) mock.mockReset();
});

describe('fixture-backed catalogue', () => {
  it('keeps the home page, /freebies and the alerts page out of search', async () => {
    expect((await homeMetadata()).robots).toEqual({ index: false });
    expect((await freebiesMetadata(search({}))).robots).toEqual({ index: false });
    const alertsMetadata = await alertsGenerateMetadata();
    expect(alertsMetadata.robots).toEqual({ index: false });
    expect(alertsMetadata.alternates?.canonical).toBe('/alerts');
  });

  it('keeps a live fixture offer out of search', async () => {
    expect((await offerMetadata(offerParams)).robots).toEqual({ index: false });
  });

  it('marks an expired fixture offer noindex, follow while keeping its canonical', async () => {
    const metadata = await offerMetadata({
      params: Promise.resolve({ slug: FIXTURE_EXPIRED.slug }),
    });

    expect(metadata.robots).toEqual({ index: false, follow: true });
    expect(metadata.alternates?.canonical).toBe(`/freebies/${FIXTURE_EXPIRED.slug}`);
  });

  it('keeps a category out of search and canonical to its own path', async () => {
    const metadata = await categoryMetadata({ params: Promise.resolve({ slug: 'beauty' }) });

    expect(metadata.robots).toEqual({ index: false });
    expect(metadata.alternates?.canonical).toBe('/category/beauty');
    expect(metadata.openGraph).toMatchObject({ url: '/category/beauty', siteName: 'Freejoy' });
  });
});

describe('once the catalogue is API-backed', () => {
  beforeEach(() => {
    mocks.source.mockResolvedValue('api');
    mocks.lookup.mockResolvedValue(found(apiOffer()));
  });

  it('makes the home page and unfiltered /freebies indexable', async () => {
    expect((await homeMetadata()).robots).toMatchObject({ index: true, follow: true });
    expect((await freebiesMetadata(search({}))).robots).toMatchObject({ index: true });
  });

  it('stays indexable on launch day, when nothing is live yet', async () => {
    mocks.featured.mockResolvedValue([]);
    mocks.search.mockResolvedValue({ items: [], page: 1, pageCount: 1, total: 0, from: 0, to: 0 });

    expect((await homeMetadata()).robots).toMatchObject({ index: true });
    expect((await freebiesMetadata(search({}))).robots).toMatchObject({ index: true });
    expect(
      (await categoryMetadata({ params: Promise.resolve({ slug: 'beauty' }) })).robots
    ).toMatchObject({ index: true });
  });

  it.each([
    ['a search', { q: 'tea' }],
    ['a category filter', { category: 'beauty' }],
    ['a type filter', { type: 'voucher' }],
    ['a postage filter', { postage: 'free' }],
    ['a sort', { sort: 'a_z' }],
    ['page 2', { page: '2' }],
  ])(
    'sends /freebies with %s to noindex, follow, canonical to /freebies',
    async (_name, params) => {
      const metadata = await freebiesMetadata(search(params));

      expect(metadata.robots).toEqual({ index: false, follow: true });
      expect(metadata.alternates?.canonical).toBe('/freebies');
    }
  );

  it('makes a category indexable, but not its filtered or paged views', async () => {
    const params = Promise.resolve({ slug: 'beauty' });

    expect((await categoryMetadata({ params })).robots).toMatchObject({ index: true });
    expect(
      (await categoryMetadata({ params, searchParams: Promise.resolve({ page: '2' }) })).robots
    ).toEqual({ index: false, follow: true });
    expect(
      (await categoryMetadata({ params, searchParams: Promise.resolve({ q: 'x' }) })).robots
    ).toEqual({ index: false, follow: true });
  });

  it('makes a live API offer indexable', async () => {
    expect((await offerMetadata(offerParams)).robots).toMatchObject({ index: true, follow: true });
  });

  it('drops the demo wording from the home page', async () => {
    render(await HomePage());
    render(await HomeSections({ demo: false }));

    expect(document.body.textContent).not.toMatch(/design preview|sample listings/i);
  });

  it('keeps the alerts page noindex until its form is live', async () => {
    expect((await alertsGenerateMetadata()).robots).toEqual({ index: false });
  });

  it('renders a real offer with no demo wording and a call to action that links through /go/', async () => {
    render(await OfferPage(offerParams));

    // The related offers below are fixtures in this test; the offer's own hero must be clean.
    const hero = screen.getByRole('heading', { level: 1 }).closest('section') as HTMLElement;
    expect(hero.textContent).not.toMatch(/demo|design preview|sample data|coming soon/i);
    const cta = within(hero).getByRole('link', { name: /^request this sample$/i });
    expect(cta).toHaveAttribute('href', `/go/${FIXTURE_LIVE.id}`);
    expect(cta).toHaveAttribute('rel', 'sponsored nofollow noopener');
    // No demo dialog button: the heart (FREEJ-28) is the only control besides the link.
    expect(
      within(hero)
        .getAllByRole('button')
        .map((button) => button.getAttribute('aria-label'))
    ).toEqual([`Save ${FIXTURE_LIVE.title}`]);
  });
});

describe('API offers that have ended', () => {
  it('answers with the ended state and noindex, follow within 30 days of the end date', async () => {
    mocks.lookup.mockResolvedValue(ended());

    const metadata = await offerMetadata(offerParams);

    expect(metadata.robots).toEqual({ index: false, follow: true });
    expect(metadata.alternates?.canonical).toBe(`/freebies/${FIXTURE_LIVE.slug}`);
    expect(metadata.title).toBe(FIXTURE_LIVE.title);
    render(await OfferPage(offerParams));
    expect(screen.getByRole('heading', { level: 1, name: FIXTURE_LIVE.title })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'This offer has ended' })).toBeInTheDocument();
    expect(screen.getByText(/ended on 24 October 2026/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Beauty' })).toHaveAttribute(
      'href',
      '/category/beauty'
    );
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('stays in the ended state when the end date is unknown', async () => {
    mocks.lookup.mockResolvedValue(ended({ endedAt: null }));

    expect((await offerMetadata(offerParams)).robots).toEqual({ index: false, follow: true });
    render(await OfferPage(offerParams));
    expect(screen.getByRole('heading', { name: 'This offer has ended' })).toBeInTheDocument();
  });

  it('is gone (not found for now) more than 30 days after the end date', async () => {
    mocks.lookup.mockResolvedValue(ended({ endedAt: '2026-09-01' }));

    expect((await offerMetadata(offerParams)).title).toBe('Offer not found');
    await expect(OfferPage(offerParams)).rejects.toThrow('NEXT_NOT_FOUND');
    expect(mocks.notFound).toHaveBeenCalledTimes(1);
  });

  it('answers not found, with noindex, for an unknown or unpublished offer', async () => {
    mocks.lookup.mockResolvedValue({ kind: 'not_found' });

    expect(await offerMetadata(offerParams)).toMatchObject({
      title: 'Offer not found',
      robots: { index: false },
    });
    await expect(OfferPage(offerParams)).rejects.toThrow('NEXT_NOT_FOUND');
  });

  it('treats an offer it cannot read exactly like one that is not found', async () => {
    mocks.lookup.mockResolvedValue({ kind: 'unreadable' });

    expect(await offerMetadata(offerParams)).toMatchObject({
      title: 'Offer not found',
      robots: { index: false },
    });
    await expect(OfferPage(offerParams)).rejects.toThrow('NEXT_NOT_FOUND');
  });
});

describe('page JSON-LD', () => {
  it('puts WebSite and Organization on the home page and nothing priced', async () => {
    const { container } = render(await HomePage());
    const nodes = jsonLdOf(container);

    expect(nodes.map((node) => node['@type'])).toEqual(['WebSite', 'Organization']);
    expect(JSON.stringify(nodes)).not.toMatch(/price|availability|rating|review|"Offer"|Product/i);
  });

  it('puts a Home > category > offer BreadcrumbList on an offer page', async () => {
    const { container } = render(await OfferPage(offerParams));
    const [breadcrumbs] = jsonLdOf(container);

    expect(breadcrumbs['@type']).toBe('BreadcrumbList');
    expect(breadcrumbs.itemListElement.map((item: { name: string }) => item.name)).toEqual([
      'Home',
      'Beauty',
      FIXTURE_LIVE.title,
    ]);
    expect(breadcrumbs.itemListElement[2].item).toBe(
      `http://localhost:3000/freebies/${FIXTURE_LIVE.slug}`
    );
  });

  it('puts a Home > category BreadcrumbList on a category page', async () => {
    // The catalogue below the intro streams through Suspense, so render only the JSON-LD child.
    const page = await CategoryPage({
      params: Promise.resolve({ slug: 'beauty' }),
      searchParams: Promise.resolve({}),
    });
    const children = (page as ReactElement<{ children: ReactElement[] }>).props.children;
    const jsonLd = children.find((child) => child.type === JsonLd);
    const { container } = render(jsonLd as ReactElement);
    const [breadcrumbs] = jsonLdOf(container);

    expect(breadcrumbs.itemListElement.map((item: { name: string }) => item.name)).toEqual([
      'Home',
      'Beauty',
    ]);
  });
});

describe('when the catalogue cannot be read (API source)', () => {
  beforeEach(() => mocks.source.mockResolvedValue('api'));

  // An outage answers 200 with the error state and keeps the page's normal robots, so a short backend
  // failure cannot de-index pages. Only a refined view is noindex, as it always is.
  it('keeps the home page indexable and shows a retryable alert instead of the catalogue', async () => {
    mocks.featured.mockImplementation(outage);

    expect((await homeMetadata()).robots).toMatchObject({ index: true });
    render(await HomeSections({ demo: false }));
    expect(screen.getByRole('alert')).toHaveTextContent(/could not load the freebies/i);
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });

  it('keeps /freebies and a category on their normal robots when the results cannot be read', async () => {
    mocks.search.mockImplementation(outage);

    expect((await freebiesMetadata(search({}))).robots).toMatchObject({ index: true });
    expect((await freebiesMetadata(search({ q: 'tea' }))).robots).toEqual({
      index: false,
      follow: true,
    });
    expect(
      (await categoryMetadata({ params: Promise.resolve({ slug: 'beauty' }) })).robots
    ).toMatchObject({ index: true });
    render(
      await CategoryPage({
        params: Promise.resolve({ slug: 'beauty' }),
        searchParams: Promise.resolve({}),
      })
    );
    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 1, name: 'Beauty freebies' })).toBeInTheDocument();
  });

  it('marks an offer page noindex, follow when the offer cannot be read, and shows the alert, never a 404', async () => {
    mocks.lookup.mockImplementation(outage);

    const metadata = await offerMetadata(offerParams);

    expect(metadata.title).toBe('Freebie unavailable');
    expect(metadata.robots).toEqual({ index: false, follow: true });
    render(await OfferPage(offerParams));
    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(mocks.notFound).not.toHaveBeenCalled();
  });

  it('marks a category page noindex, follow when the category cannot be read', async () => {
    mocks.search.mockImplementation(outage);
    const { CatalogueUnavailableError: Outage } = await import('@/lib/catalogue/errors');
    const lookupFails = vi.spyOn(await import('@/lib/catalogue'), 'getCategoryBySlug');
    lookupFails.mockRejectedValue(new Outage());

    const metadata = await categoryMetadata({ params: Promise.resolve({ slug: 'beauty' }) });

    expect(metadata.title).toBe('Freebies unavailable');
    expect(metadata.robots).toEqual({ index: false, follow: true });
    lookupFails.mockRestore();
  });

  it('lets an error that is not an outage through instead of calling it an outage', async () => {
    mocks.lookup.mockRejectedValue(new Error('a real bug'));

    await expect(offerMetadata(offerParams)).rejects.toThrow('a real bug');
  });
});

describe('when the catalogue cannot be read (fixture source)', () => {
  it('stays noindex', async () => {
    mocks.source.mockResolvedValue('fixture');
    mocks.search.mockImplementation(outage);
    mocks.featured.mockImplementation(outage);

    expect((await homeMetadata()).robots).toEqual({ index: false });
    expect((await freebiesMetadata(search({}))).robots).toEqual({ index: false });
    expect(
      (await categoryMetadata({ params: Promise.resolve({ slug: 'beauty' }) })).robots
    ).toEqual({ index: false });
  });
});

describe('refined views', () => {
  beforeEach(() => mocks.source.mockResolvedValue('api'));

  it('treats a category page carrying another ?category= as noindex, follow', async () => {
    const metadata = await categoryMetadata({
      params: Promise.resolve({ slug: 'beauty' }),
      searchParams: Promise.resolve({ category: 'pets' }),
    });

    expect(metadata.robots).toEqual({ index: false, follow: true });
    expect(metadata.alternates?.canonical).toBe('/category/beauty');
  });

  it('uses a fixed Open Graph title on /freebies while the <title> follows the search', async () => {
    const metadata = await freebiesMetadata(search({ q: 'pet' }));

    expect(metadata.title).toBe('Search: pet');
    expect(metadata.openGraph?.title).toBe('All freebies');
  });
});

describe('offer page JSON-LD with hostile text', () => {
  it('renders no raw < inside the script when the title contains a closing script tag', async () => {
    const title = 'Tea </script><!-- deal';
    mocks.lookup.mockResolvedValue(found(apiOffer({ title })));

    const { container } = render(await OfferPage(offerParams));
    const script = container.querySelector('script[type="application/ld+json"]');

    expect(script?.textContent).not.toContain('<');
    expect(JSON.parse(script?.textContent ?? '{}').itemListElement[2].name).toBe(title);
    expect(container.querySelectorAll('script')).toHaveLength(1);
  });
});
