import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';

const mocks = vi.hoisted(() => ({ search: vi.fn(), source: vi.fn() }));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ refresh: vi.fn() }),
  usePathname: () => '/',
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock('@/lib/catalogue', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/catalogue')>();
  return {
    ...actual,
    searchOffers: (...args: Parameters<typeof actual.searchOffers>) =>
      mocks.search.getMockImplementation() ? mocks.search(...args) : actual.searchOffers(...args),
    getCatalogueSource: () =>
      mocks.source.getMockImplementation() ? mocks.source() : actual.getCatalogueSource(),
  };
});

import { CatalogueHeader } from '@/components/catalogue/catalogue-header';
import {
  CatalogueHeading,
  CatalogueResults,
  CatalogueResultsSkeleton,
} from '@/components/catalogue/catalogue-results';
import { OfferGrid } from '@/components/catalogue/offer-card';
import { FreshlyFound } from '@/components/home/freshly-found';
import { HomeHero } from '@/components/home/home-hero';
import { HomeSectionsSkeleton } from '@/components/home/home-sections';
import { TrustStrip } from '@/components/home/trust-strip';
import { EndedOfferView } from '@/components/offer/ended-offer';
import { OfferCta } from '@/components/offer/offer-cta';
import { CatalogueUnavailableError, DEFAULT_QUERY } from '@/lib/catalogue';
import { MERCHANTS, OFFERS } from '@/lib/catalogue/fixtures';
import type { Offer } from '@/lib/catalogue/types';

const fixtureOffer = OFFERS[0] as Offer;
const apiOffer: Offer = {
  ...fixtureOffer,
  id: 'api-1',
  slug: 'api-offer',
  source: 'api',
  demo: false,
  merchant: { slug: 'api-merchant', name: 'API Merchant', logoUrl: null },
};
const NO_RESULTS = { items: [], page: 1, pageCount: 1, total: 0, from: 0, to: 0 };

beforeEach(() => {
  mocks.search.mockReset();
  mocks.source.mockReset();
});

describe('offer call to action for a real offer', () => {
  it('links through the first-party redirect, with no demo wording and no external link', () => {
    render(
      <OfferCta
        sponsored={false}
        earnsCommission
        offerType="free_sample"
        demo={false}
        offerId="api-1"
      />
    );

    const link = screen.getByRole('link', { name: 'Request this sample' });
    expect(link).toHaveAttribute('href', '/go/api-1');
    expect(link).toHaveAttribute('rel', 'sponsored nofollow noopener');
    expect(screen.queryByRole('button')).toBeNull();
    expect(document.body.textContent).not.toMatch(/design preview|sample data|demo|coming soon/i);
    for (const anchor of screen.getAllByRole('link')) {
      expect(anchor.getAttribute('href') ?? '').not.toMatch(/^(https?:)?\/\//);
    }
  });

  it('leaves the demo button exactly as it was, with no coming-soon badge', () => {
    render(<OfferCta sponsored={false} earnsCommission offerType="free_sample" demo />);

    const button = screen.getByRole('button', { name: 'Request this sample' });
    expect(button).not.toHaveTextContent(/coming soon/i);
    expect(button).not.toHaveAttribute('aria-label');
  });

  it('keeps the affiliate disclosure link beside the real offer button', () => {
    render(
      <OfferCta
        sponsored={false}
        earnsCommission
        offerType="voucher"
        demo={false}
        offerId="api-1"
      />
    );

    expect(screen.getByRole('link', { name: /affiliate disclosure/i })).toHaveAttribute(
      'href',
      '/affiliate-disclosure'
    );
  });
});

describe('ended offer view', () => {
  const ended = {
    slug: 'old-tea',
    title: 'Old tea sample',
    categorySlug: 'food-drink',
    categoryName: 'Food & drink',
    endedAt: '2026-05-31',
  };

  it('shows the title, the breadcrumb, the end date and the two ways onward', () => {
    render(<EndedOfferView ended={ended} />);

    expect(screen.getByRole('heading', { level: 1, name: 'Old tea sample' })).toBeInTheDocument();
    const trail = screen.getByRole('navigation', { name: 'Breadcrumb' });
    expect(within(trail).getByRole('link', { name: 'Food & drink' })).toHaveAttribute(
      'href',
      '/category/food-drink'
    );
    expect(screen.getByRole('heading', { name: 'This offer has ended' })).toBeInTheDocument();
    expect(screen.getByText(/ended on 31 May 2026/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /more in this category/i })).toHaveAttribute(
      'href',
      '/category/food-drink'
    );
    expect(screen.getByRole('link', { name: /browse all freebies/i })).toHaveAttribute(
      'href',
      '/freebies'
    );
  });

  it('leaves the date out when the end date is unknown', () => {
    render(<EndedOfferView ended={{ ...ended, endedAt: null }} />);

    expect(screen.queryByText(/ended on/i)).toBeNull();
    expect(screen.getByText(/no longer accepting requests/i)).toBeInTheDocument();
  });

  it('reveals nothing but the stub: no call to action, no claim steps, no terms', () => {
    render(<EndedOfferView ended={ended} />);

    expect(screen.queryByRole('button')).toBeNull();
    expect(screen.queryByText(/how to claim/i)).toBeNull();
  });
});

describe('live versus demo wording', () => {
  it('keeps the design-preview wording only for fixtures', () => {
    const { unmount } = render(
      <>
        <HomeHero demo />
        <TrustStrip demo />
      </>
    );
    expect(screen.getByText('Design preview with sample listings')).toBeInTheDocument();
    expect(screen.getAllByText('Design preview').length).toBeGreaterThan(0);
    unmount();

    render(
      <>
        <HomeHero demo={false} />
        <TrustStrip demo={false} />
      </>
    );
    expect(document.body.textContent).not.toMatch(/design preview|sample listings/i);
    expect(screen.getByText('Read the terms')).toBeInTheDocument();
  });

  it('shows four trust items either way', () => {
    const { rerender } = render(<TrustStrip demo />);
    expect(screen.getAllByRole('listitem')).toHaveLength(4);
    rerender(<TrustStrip demo={false} />);
    expect(screen.getAllByRole('listitem')).toHaveLength(4);
  });

  it('drops the sample note from the catalogue intro and the featured section', () => {
    render(
      <>
        <CatalogueHeader query={DEFAULT_QUERY} demo={false} />
        <FreshlyFound offers={[apiOffer]} merchants={[]} demo={false} />
      </>
    );

    expect(document.body.textContent).not.toMatch(
      /design preview|sample listings|sample catalogue/i
    );
    expect(screen.getByText('Featured picks from our latest finds.')).toBeInTheDocument();
  });

  it('keeps the sample note for fixtures', () => {
    render(
      <>
        <CatalogueHeader query={DEFAULT_QUERY} demo />
        <FreshlyFound offers={[fixtureOffer]} merchants={MERCHANTS} demo />
      </>
    );

    expect(screen.getByText(/sample listings for the design preview\./i)).toBeInTheDocument();
    expect(
      screen.getByText('Sample listings for the design preview, not real offers.')
    ).toBeInTheDocument();
  });

  it('says there is nothing featured instead of showing an empty grid', () => {
    render(<FreshlyFound offers={[]} merchants={[]} demo={false} />);

    expect(screen.getByText(/no featured freebies right now/i)).toBeInTheDocument();
    expect(screen.queryByRole('list')).toBeNull();
  });
});

describe('OfferGrid', () => {
  it('names an API offer’s merchant from the offer itself and shows no demo badge', () => {
    render(<OfferGrid offers={[apiOffer]} merchants={[]} />);

    expect(screen.getByText('API Merchant')).toBeInTheDocument();
    expect(screen.queryByText('Demo')).toBeNull();
  });

  it('falls back to the merchant list, then the slug, for a fixture offer', () => {
    const { unmount } = render(<OfferGrid offers={[fixtureOffer]} merchants={MERCHANTS} />);
    const name = MERCHANTS.find((merchant) => merchant.slug === fixtureOffer.merchantSlug)?.name;
    expect(screen.getByText(name as string)).toBeInTheDocument();
    unmount();

    render(<OfferGrid offers={[fixtureOffer]} merchants={[]} />);
    expect(screen.getByText(fixtureOffer.merchantSlug)).toBeInTheDocument();
  });
});

describe('catalogue results when the catalogue cannot be read', () => {
  it('shows a retryable alert in place of the results, and no count in the heading', async () => {
    mocks.search.mockRejectedValue(new CatalogueUnavailableError());

    render(await CatalogueResults({ query: DEFAULT_QUERY }));
    render(await CatalogueHeading({ query: DEFAULT_QUERY }));

    expect(screen.getByRole('alert')).toHaveTextContent(/could not load the freebies/i);
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 2, name: 'All freebies' })).toBeInTheDocument();
    expect(screen.queryByText(/in this collection/i)).toBeNull();
    expect(screen.queryByRole('article')).toBeNull();
  });

  it('keeps the category name in the heading when only the search fails', async () => {
    mocks.search.mockRejectedValue(new CatalogueUnavailableError());

    render(await CatalogueHeading({ query: { ...DEFAULT_QUERY, category: 'pets' } }));

    expect(screen.getByRole('heading', { level: 2, name: 'Pets' })).toBeInTheDocument();
    expect(screen.queryByText(/in this collection/i)).toBeNull();
  });

  it('does not swallow an error that is not an outage', async () => {
    mocks.search.mockRejectedValue(new Error('a real bug'));

    await expect(CatalogueResults({ query: DEFAULT_QUERY })).rejects.toThrow('a real bug');
  });

  it('shows the real empty state, not an error, when nothing matches', async () => {
    mocks.search.mockResolvedValue(NO_RESULTS);

    render(await CatalogueResults({ query: DEFAULT_QUERY }));

    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.getByRole('heading', { name: 'No freebies match that yet' })).toBeInTheDocument();
  });

  it('drops the sample sentence from the terms note when API-backed', async () => {
    mocks.source.mockResolvedValue('api');
    mocks.search.mockResolvedValue(NO_RESULTS);

    render(await CatalogueResults({ query: DEFAULT_QUERY }));

    expect(screen.getByText(/availability can change quickly/i).textContent).not.toMatch(/sample/i);
  });
});

describe('loading skeletons', () => {
  it('marks the results skeleton busy with an accessible name, and hides the blocks', () => {
    const { container } = render(<CatalogueResultsSkeleton />);

    const status = screen.getByRole('status', { name: 'Loading freebies' });
    expect(status).toHaveAttribute('aria-busy', 'true');
    expect(container.querySelectorAll('[aria-hidden="true"]').length).toBeGreaterThan(0);
  });

  it('marks the home skeleton busy too', () => {
    render(<HomeSectionsSkeleton />);

    expect(screen.getByRole('status', { name: 'Loading freebies' })).toHaveAttribute(
      'aria-busy',
      'true'
    );
  });
});
