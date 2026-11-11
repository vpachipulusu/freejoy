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
  it('reads "Sponsored listing": the word is visible text and " listing" completes it for a screen reader', () => {
    render(<SponsoredBadge />);

    const badge = screen.getByRole('note');
    expect(badge).toHaveTextContent('Sponsored listing');
    expect(badge).not.toHaveAttribute('aria-label');
    expect(badge).not.toHaveAttribute('aria-hidden');
    expect(badge.closest('[aria-hidden="true"]')).toBeNull();
    expect(badge.firstChild?.textContent).toBe('Sponsored');
    expect(badge.querySelector('.sr-only')?.textContent).toBe(' listing');
  });

  it('takes an id so a link can be described by it', () => {
    render(<SponsoredBadge id="badge-1" />);

    expect(screen.getByRole('note')).toHaveAttribute('id', 'badge-1');
  });
});

// jsdom does no layout, so these only check the class names of the badge element itself (the inner sr-only span is
// meant to be visually hidden). They are not a check that the badge fits at 320px; that is not browser verified.
const HIDING_OR_CLIPPING =
  /(^|:)(hidden|invisible|truncate|overflow-hidden|absolute|w-0|opacity-0)$|line-clamp|(^|:)max-w-|(^|:)max-h-/;

function expectNoHidingClasses(badge: HTMLElement) {
  const classes = badge.className.split(/\s+/);
  expect(classes).toContain('border');
  expect(classes.filter((name) => HIDING_OR_CLIPPING.test(name))).toEqual([]);
}

describe('the badge as rendered by the card and the page', () => {
  it('has no class that hides, clips or shrinks it on a card', () => {
    render(<OfferCard offer={sponsored} merchant={merchant} />);

    expectNoHidingClasses(screen.getByRole('note'));
  });

  it('has no class that hides, clips or shrinks it on the detail page', () => {
    hero(sponsored);

    expectNoHidingClasses(screen.getByRole('note'));
  });
});

describe('OfferCard sponsored label', () => {
  it('shows the label on a sponsored offer, outside the art, next to the merchant', () => {
    render(<OfferCard offer={sponsored} merchant={merchant} />);

    const badge = screen.getByRole('note');
    expect(badge).toHaveTextContent('Sponsored listing');
    expect(badge).toBeVisible();
    expect(badge.closest('article')).not.toBeNull();
    expect(
      within(screen.getByRole('img', { name: base.image.alt }).parentElement!).queryByRole('note')
    ).toBeNull();
  });

  it('describes the card link with the badge, so link and heading navigation hear "Sponsored listing"', () => {
    render(<OfferCard offer={sponsored} merchant={merchant} />);

    const link = screen.getByRole('link', { name: base.title });
    expect(link).toHaveAccessibleDescription('Sponsored listing');
    expect(link.getAttribute('aria-describedby')).toBe(screen.getByRole('note').id);
  });

  it('gives each card its own badge id, so two sponsored cards never share a description', () => {
    render(
      <>
        <OfferCard offer={sponsored} merchant={merchant} />
        <OfferCard
          offer={{ ...sponsored, slug: 'other', title: 'Other offer' }}
          merchant={merchant}
        />
      </>
    );

    const ids = screen.getAllByRole('note').map((note) => note.id);
    expect(new Set(ids).size).toBe(2);
  });

  it('gives the link of an offer that is not sponsored no description', () => {
    render(<OfferCard offer={{ ...base, sponsored: false }} merchant={merchant} />);

    const link = screen.getByRole('link', { name: base.title });
    expect(link).not.toHaveAttribute('aria-describedby');
    expect(link).toHaveAccessibleDescription('');
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

    expect(screen.getByRole('note')).toHaveTextContent('Sponsored listing');
  });

  it('shows nothing for an offer that is not sponsored', () => {
    hero({ ...base, sponsored: false });

    expect(screen.queryByRole('note')).toBeNull();
  });
});

describe('the call to action of a sponsored offer', () => {
  it('says in a sentence that the brand pays for the listing, and still links with the paid-link rel', () => {
    render(
      <OfferCta earnsCommission offerType="free_sample" demo={false} offerId="offer-9" sponsored />
    );

    expect(screen.getByText(/sponsored listing: the brand pays Freejoy/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /request this sample/i })).toHaveAttribute(
      'rel',
      'sponsored nofollow noopener'
    );
  });

  it('describes the outbound link with that sentence, and with the conditions line when there is one', () => {
    render(
      <OfferCta
        earnsCommission
        offerType="free_sample"
        demo={false}
        offerId="offer-9"
        sponsored
        conditions="Postage: free"
      />
    );

    const link = screen.getByRole('link', { name: /request this sample/i });
    expect(link).toHaveAccessibleDescription(/Postage: free.*sponsored listing/i);
    expect(link.getAttribute('aria-describedby')?.split(' ')).toHaveLength(2);
  });

  it('describes the link with the sponsored sentence alone when there are no conditions', () => {
    render(
      <OfferCta earnsCommission offerType="free_sample" demo={false} offerId="offer-9" sponsored />
    );

    expect(screen.getByRole('link', { name: /request this sample/i })).toHaveAccessibleDescription(
      /sponsored listing/i
    );
  });

  it('does not state as fact that a commission is paid on a sponsored offer: it says it may be', () => {
    render(
      <OfferCta earnsCommission offerType="free_sample" demo={false} offerId="offer-9" sponsored />
    );

    expect(screen.queryByText(/is paid a commission by the brand/i)).toBeNull();
    expect(
      screen.getByText(/may also earn a commission if you use this link/i)
    ).toBeInTheDocument();
  });

  it('keeps the commission sentence for an offer that is not sponsored', () => {
    render(
      <OfferCta
        sponsored={false}
        earnsCommission
        offerType="free_sample"
        demo={false}
        offerId="offer-9"
      />
    );

    expect(screen.getByText(/is paid a commission by the brand/i)).toBeInTheDocument();
    expect(screen.queryByText(/may also earn/i)).toBeNull();
  });

  it('does not add that sentence when the offer is not sponsored', () => {
    render(
      <OfferCta
        sponsored={false}
        earnsCommission
        offerType="free_sample"
        demo={false}
        offerId="offer-9"
      />
    );

    expect(screen.queryByText(/sponsored listing/i)).toBeNull();
  });

  it('keeps the outbound rel exactly as it was for every basis', () => {
    expect(OUTBOUND_LINK_REL).toBe('sponsored nofollow noopener');
  });
});
