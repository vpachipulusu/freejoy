/**
 * The "Sponsored" label (FREEJ-53): a text badge on the card and on the detail page, present only for a
 * sponsored offer, announced to screen readers, never hidden at small widths, and the outbound link rel stays.
 */
import { describe, expect, it } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { OfferCard } from '@/components/catalogue/offer-card';
import { SponsoredBadge } from '@/components/catalogue/sponsored-badge';
import { OfferCta } from '@/components/offer/offer-cta';
import { OfferHero } from '@/components/offer/offer-hero';
import { OUTBOUND_LINK_REL } from '@/components/offer/outbound-link';
import { CATEGORIES, MERCHANTS, OFFERS } from '@/lib/catalogue/fixtures';
import { toCardOffer, type Offer } from '@/lib/catalogue/types';

const base: Offer = OFFERS[0];
const merchant = MERCHANTS.find((item) => item.slug === base.merchantSlug)!;
const category = CATEGORIES.find((item) => item.slug === base.categorySlug)!;
const sponsored: Offer = { ...base, sponsored: true };

function hero(offer: Offer) {
  return render(<OfferHero offer={offer} merchant={merchant} category={category} ended={false} />);
}

describe('SponsoredBadge', () => {
  it('says "Sponsored" in words and has an accessible name a screen reader announces', () => {
    render(<SponsoredBadge />);

    const badge = screen.getByRole('note', { name: 'Sponsored listing' });
    expect(badge).toHaveTextContent('Sponsored');
    expect(badge).not.toHaveAttribute('aria-hidden');
    expect(badge.closest('[aria-hidden="true"]')).toBeNull();
  });

  it('is a bordered pill, not a colour on its own, and has no rule that hides it on a narrow screen', () => {
    render(<SponsoredBadge />);

    const classes = screen.getByRole('note').className.split(/\s+/);
    expect(classes).toContain('border');
    expect(classes.filter((name) => /hidden|invisible|sr-only/.test(name))).toEqual([]);
  });
});

describe('OfferCard sponsored label', () => {
  it('shows the label on a sponsored offer, outside the art, next to the merchant', () => {
    render(<OfferCard offer={sponsored} merchant={merchant} />);

    const badge = screen.getByRole('note', { name: 'Sponsored listing' });
    expect(badge).toBeVisible();
    expect(badge.closest('article')).not.toBeNull();
    expect(
      within(screen.getByRole('img', { name: base.image.alt }).parentElement!).queryByRole('note')
    ).toBeNull();
  });

  it('shows nothing for an offer that is not sponsored', () => {
    render(<OfferCard offer={{ ...base, sponsored: false }} merchant={merchant} />);

    expect(screen.queryByRole('note')).toBeNull();
    expect(screen.queryByText('Sponsored')).toBeNull();
  });

  it('shows the label on the card projection the Saved page sends, which has no other basis field', () => {
    render(<OfferCard offer={toCardOffer({ ...sponsored, featured: true })} merchant={merchant} />);

    expect(screen.getByText('Sponsored')).toBeVisible();
  });
});

describe('detail page hero sponsored label', () => {
  it('shows the label beside the merchant and category of a sponsored offer', () => {
    hero(sponsored);

    expect(screen.getByRole('note', { name: 'Sponsored listing' })).toBeVisible();
  });

  it('shows nothing for an offer that is not sponsored', () => {
    hero({ ...base, sponsored: false });

    expect(screen.queryByRole('note')).toBeNull();
  });
});

describe('the call to action of a sponsored offer', () => {
  it('says in a sentence that the brand pays for the listing, and still links with the paid-link rel', () => {
    render(<OfferCta offerType="free_sample" demo={false} offerId="offer-9" sponsored />);

    expect(screen.getByText(/sponsored listing: the brand pays Freejoy/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /request this sample/i })).toHaveAttribute(
      'rel',
      'sponsored nofollow noopener'
    );
  });

  it('does not add that sentence when the offer is not sponsored', () => {
    render(<OfferCta offerType="free_sample" demo={false} offerId="offer-9" />);

    expect(screen.queryByText(/sponsored listing/i)).toBeNull();
  });

  it('keeps the outbound rel exactly as it was for every basis', () => {
    expect(OUTBOUND_LINK_REL).toBe('sponsored nofollow noopener');
  });
});
