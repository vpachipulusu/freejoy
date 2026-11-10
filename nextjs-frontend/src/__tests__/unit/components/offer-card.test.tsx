import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { OfferCard, OfferGrid } from '@/components/catalogue/offer-card';
import { MERCHANTS, OFFERS } from '@/lib/catalogue/fixtures';
import { toCardOffer, type Offer } from '@/lib/catalogue/types';

const offer = OFFERS[0];
const merchant = MERCHANTS.find((item) => item.slug === offer.merchantSlug)!;

describe('OfferCard', () => {
  it('links the title to the offer page and labels the offer kind honestly', () => {
    render(<OfferCard offer={offer} merchant={merchant} />);

    expect(screen.getByRole('link', { name: offer.title })).toHaveAttribute(
      'href',
      `/freebies/${offer.slug}`
    );
    expect(screen.getByText(merchant.name)).toBeInTheDocument();
    expect(screen.getByRole('img', { name: offer.image.alt })).toBeInTheDocument();
  });

  it('leaves the keyboard focus to the card: no ring, and the stretched title link draws no outline of its own', () => {
    const { container } = render(<OfferCard offer={offer} merchant={merchant} />);

    expect(container.querySelector('article')!.className).not.toMatch(/(^|[\s:])ring(-|\s|$)/);
    expect(container.querySelector('article')).toHaveClass('focus-proxy');
    expect(screen.getByRole('link', { name: offer.title }).className).toContain(
      'focus-visible:outline-none'
    );
  });

  it('shows the demo badge on fixture offers and never links off-site', () => {
    const { container } = render(<OfferCard offer={offer} merchant={merchant} />);

    expect(screen.getByText('Demo')).toBeInTheDocument();
    expect(container.querySelector('a[href^="http"]')).toBeNull();
  });
});

describe('OfferCard layout on narrow cards (M4) and hover groups (M5)', () => {
  it('lays the art and the demo badge in their own box, with the type badge and heart outside it', () => {
    const { container } = render(<OfferCard offer={offer} merchant={merchant} />);
    const art = screen.getByRole('img', { name: offer.image.alt });
    const artBox = art.parentElement!;

    expect(artBox).toHaveClass('relative');
    expect(artBox).toContainElement(screen.getByText('Demo'));
    const outer = artBox.parentElement!;
    const badge = screen.getByText(/free sample|free trial|cashback|voucher|competition/i);
    expect(artBox).not.toContainElement(badge);
    expect(outer).toContainElement(badge);
    expect(outer).toContainElement(screen.getByRole('button', { name: /^Save / }));
    expect(container.querySelectorAll('article')).toHaveLength(1);
  });

  it('keeps the type badge on one line, and drops it below the art under 375px so it cannot meet the heart', () => {
    render(<OfferCard offer={offer} merchant={merchant} />);
    const badge = screen.getByText(/free sample|free trial|cashback|voucher|competition/i);

    for (const name of [
      'absolute',
      'left-2',
      'top-2',
      'whitespace-nowrap',
      'max-[374px]:static',
      'max-[374px]:mt-2',
      'max-[374px]:border',
      'sm:left-3',
      'sm:top-3',
    ]) {
      expect(badge).toHaveClass(name);
    }
  });

  it('uses named hover groups: hovering the heart must not underline "View freebie"', () => {
    const { container } = render(<OfferCard offer={offer} merchant={merchant} />);
    const article = container.querySelector('article')!;
    const label = screen.getByText('View freebie');

    expect(article).toHaveClass('group/card');
    expect(label).toHaveClass('group-hover/card:underline', 'group-has-[button:hover]/card:no-underline');
    expect(article.className).not.toMatch(/(^|\s)group(\s|$)/);
    expect(label.className).not.toMatch(/(^|\s)group-hover:/);
  });

  it('accepts the card-shaped projection the Saved page sends, not only a whole offer', () => {
    const projection = toCardOffer(offer);

    render(<OfferCard offer={projection} merchant={merchant} />);

    expect(screen.getByRole('link', { name: offer.title })).toBeInTheDocument();
  });
});

describe('OfferCard postage for samples', () => {
  const sample = OFFERS.find((item) => item.offerType === 'free_sample');
  if (!sample) throw new Error('The fixtures must contain a free_sample offer');
  const sampleMerchant = MERCHANTS.find((item) => item.slug === sample.merchantSlug)!;
  const withPostage = (postage: Offer['postage'], over: Partial<Offer> = {}) => ({
    ...sample,
    postage,
    ...over,
  });

  it.each([
    [{ kind: 'free' }, 'Postage: Free'],
    [{ kind: 'cost', amountMinor: 299, currency: 'GBP' }, 'Postage: £2.99'],
    [{ kind: 'check_terms' }, 'Postage: Check terms'],
  ] as const)('shows the postage for a free sample (%j)', (postage, text) => {
    render(<OfferCard offer={withPostage(postage)} merchant={sampleMerchant} />);

    expect(screen.getByText(text)).toBeInTheDocument();
  });

  it('shows it for a real (API) sample too', () => {
    render(
      <OfferCard
        offer={withPostage(
          { kind: 'cost', amountMinor: 150, currency: 'GBP' },
          { source: 'api', demo: false }
        )}
        merchant={sampleMerchant}
      />
    );

    expect(screen.getByText('Postage: £1.50')).toBeInTheDocument();
  });

  it.each(['free_trial', 'cashback', 'voucher', 'competition'] as const)(
    'shows no postage line for %s',
    (offerType) => {
      render(
        <OfferCard offer={withPostage({ kind: 'free' }, { offerType })} merchant={sampleMerchant} />
      );

      expect(screen.queryByText(/^Postage:/)).toBeNull();
    }
  );

  it('keeps the type badge wording and puts the postage in its own line, outside the footer row', () => {
    const { container } = render(
      <OfferCard offer={withPostage({ kind: 'free' })} merchant={sampleMerchant} />
    );

    expect(screen.getByText('Free sample')).toBeInTheDocument();
    const line = screen.getByText('Postage: Free');
    expect(line.tagName).toBe('P');
    expect(container.querySelector('.border-t')?.contains(line)).toBe(false);
  });
});

describe('OfferGrid', () => {
  it('renders one list item per offer', () => {
    render(<OfferGrid offers={OFFERS.slice(0, 4)} merchants={MERCHANTS} />);

    expect(screen.getAllByRole('listitem')).toHaveLength(4);
  });
});

describe('OfferCard urgency and popularity labels', () => {
  const withLabel = (freshness: 'popular' | 'ends_soon' | 'limited', over = {}) => ({
    ...offer,
    freshness,
    ...over,
  });

  it.each([
    ['popular', 'Popular'],
    ['ends_soon', 'Ends soon'],
    ['limited', 'Limited'],
  ] as const)('does not show "%s" on a real API offer', (freshness, text) => {
    render(
      <OfferCard offer={withLabel(freshness, { source: 'api', demo: false })} merchant={merchant} />
    );

    expect(screen.queryByText(text)).toBeNull();
  });

  it('still shows the label on a demo fixture offer, next to the demo badge', () => {
    render(<OfferCard offer={withLabel('ends_soon')} merchant={merchant} />);

    expect(screen.getByText('Ends soon')).toBeInTheDocument();
    expect(screen.getByText('Demo')).toBeInTheDocument();
  });

  it('keeps a recency label on a real API offer', () => {
    render(
      <OfferCard
        offer={{ ...offer, freshness: 'just_added', source: 'api', demo: false }}
        merchant={merchant}
      />
    );

    expect(screen.getByText('Just added')).toBeInTheDocument();
  });
});
