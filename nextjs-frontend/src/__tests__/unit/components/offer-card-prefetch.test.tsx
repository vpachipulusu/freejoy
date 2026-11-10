import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

// A Link stand-in that shows the prefetch prop it was given.
vi.mock('next/link', () => ({
  default: ({
    href,
    prefetch,
    children,
    ...rest
  }: Record<string, unknown> & { children: unknown }) => (
    <a href={href as string} data-prefetch={String(prefetch)} {...rest}>
      {children as never}
    </a>
  ),
}));
const load = vi.hoisted(() => vi.fn());
vi.mock('@/app/actions/saved-actions', () => ({ loadSavedOffersAction: load }));

import { OfferCard, OfferGrid } from '@/components/catalogue/offer-card';
import { SavedOffers } from '@/components/saved/saved-offers';
import { MERCHANTS, OFFERS } from '@/lib/catalogue/fixtures';
import { toCardOffer } from '@/lib/catalogue/types';
import { SAVED_STORAGE_KEY } from '@/lib/saved/saved-store';

const offer = OFFERS[0];
const merchant = MERCHANTS.find((item) => item.slug === offer.merchantSlug)!;
const titleLink = () => screen.getByRole('link', { name: offer.title });

beforeEach(() => {
  window.localStorage.clear();
  load.mockReset();
});

describe('card link prefetching', () => {
  it('leaves Next.js to decide by default, as before (everywhere but the Saved page)', () => {
    render(<OfferCard offer={offer} merchant={merchant} />);

    expect(titleLink()).toHaveAttribute('data-prefetch', 'undefined');
  });

  it('turns prefetch off when told to', () => {
    render(<OfferCard offer={offer} merchant={merchant} prefetch={false} />);

    expect(titleLink()).toHaveAttribute('data-prefetch', 'false');
  });

  it('passes the setting through the grid, and defaults the same way', () => {
    const { unmount } = render(<OfferGrid offers={[offer]} merchants={[merchant]} />);
    expect(titleLink()).toHaveAttribute('data-prefetch', 'undefined');
    unmount();

    render(<OfferGrid offers={[offer]} merchants={[merchant]} prefetch={false} />);
    expect(titleLink()).toHaveAttribute('data-prefetch', 'false');
  });

  it('is off for every card on the Saved page, so /saved does not prefetch the whole list', async () => {
    window.localStorage.setItem(
      SAVED_STORAGE_KEY,
      JSON.stringify({ v: 1, slugs: [OFFERS[0].slug, OFFERS[1].slug] })
    );
    load.mockResolvedValue({
      success: true,
      data: {
        offers: [toCardOffer(OFFERS[0]), toCardOffer(OFFERS[1])],
        missing: [],
        unavailable: [],
      },
    });

    render(<SavedOffers />);

    await waitFor(() => expect(screen.getAllByRole('heading', { level: 3 })).toHaveLength(2));
    const links = screen
      .getAllByRole('link', { name: /./ })
      .filter((link) => link.getAttribute('href')?.startsWith('/freebies/'));
    expect(links).toHaveLength(2);
    for (const link of links) expect(link).toHaveAttribute('data-prefetch', 'false');
  });
});
