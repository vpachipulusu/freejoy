import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { OfferCta } from '@/components/offer/offer-cta';
import { OfferHero } from '@/components/offer/offer-hero';
import { CTA_LABELS, REQUEST_KIND } from '@/components/offer/offer-format';
import { AffiliateCta, OutboundLink, OUTBOUND_LINK_REL } from '@/components/offer/outbound-link';
import { OFFER_TYPES } from '@/lib/catalogue/types';
import { CATEGORIES, MERCHANTS, OFFERS } from '@/lib/catalogue/fixtures';

function goAnchors(container: HTMLElement): HTMLAnchorElement[] {
  return Array.from(container.querySelectorAll<HTMLAnchorElement>('a')).filter((anchor) =>
    (anchor.getAttribute('href') ?? '').startsWith('/go/')
  );
}

describe('OutboundLink', () => {
  it('is a plain anchor to the first-party redirect with the paid-link rel values', () => {
    render(
      <OutboundLink commissionDisclosed offerId="offer_1">
        Go
      </OutboundLink>
    );

    const link = screen.getByRole('link', { name: 'Go' });
    expect(link).toHaveAttribute('href', '/go/offer_1');
    expect(link).toHaveAttribute('rel', 'sponsored nofollow noopener');
    expect(OUTBOUND_LINK_REL).toBe('sponsored nofollow noopener');
    expect(link).not.toHaveAttribute('target');
    expect(link).not.toHaveAttribute('data-prefetch');
  });

  it('encodes the id so it cannot add path segments or a query', () => {
    render(
      <OutboundLink offerId="a/b?x=1" commissionDisclosed>
        Go
      </OutboundLink>
    );

    expect(screen.getByRole('link')).toHaveAttribute('href', '/go/a%2Fb%3Fx%3D1');
  });

  it('adds ?nd=1 (not disclosed) only when the page shows no commission disclosure', () => {
    render(
      <>
        <OutboundLink offerId="offer_1" commissionDisclosed>
          Disclosed
        </OutboundLink>
        <OutboundLink offerId="offer_1" commissionDisclosed={false}>
          Undisclosed
        </OutboundLink>
      </>
    );

    expect(screen.getByRole('link', { name: 'Disclosed' })).toHaveAttribute('href', '/go/offer_1');
    expect(screen.getByRole('link', { name: 'Undisclosed' })).toHaveAttribute(
      'href',
      '/go/offer_1?nd=1'
    );
    expect(screen.getByRole('link', { name: 'Undisclosed' })).toHaveAttribute(
      'rel',
      'sponsored nofollow noopener'
    );
  });
});

describe('AffiliateCta', () => {
  it.each(OFFER_TYPES)('links a %s offer through /go/ with its own honest label', (offerType) => {
    const { container } = render(
      <AffiliateCta sponsored={false} earnsCommission offerId="offer_1" offerType={offerType} />
    );

    const link = screen.getByRole('link', { name: CTA_LABELS[offerType] });
    expect(link).toHaveAttribute('href', '/go/offer_1');
    expect(link).toHaveAttribute('rel', 'sponsored nofollow noopener');
    expect(container).toHaveTextContent(`request your ${REQUEST_KIND[offerType]}`);
  });

  it('puts the Ad label before the button and states the commission plainly', () => {
    const { container } = render(
      <AffiliateCta sponsored={false} earnsCommission offerId="offer_1" offerType="free_sample" />
    );

    const label = screen.getByText('Ad');
    const link = screen.getByRole('link', { name: CTA_LABELS.free_sample });
    expect(label.compareDocumentPosition(link) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(label).not.toHaveAttribute('aria-hidden');
    expect(container).toHaveTextContent(
      'Freejoy is paid a commission by the brand if you take up this offer through this link.'
    );
    expect(container).not.toHaveTextContent(/may earn/i);
    expect(screen.getByRole('link', { name: /affiliate disclosure/i })).toHaveAttribute(
      'href',
      '/affiliate-disclosure'
    );
  });

  it('renders no external href: the only way out is the redirect', () => {
    render(
      <AffiliateCta sponsored={false} earnsCommission offerId="offer_1" offerType="cashback" />
    );

    for (const link of screen.getAllByRole('link')) {
      expect(link.getAttribute('href') ?? '').not.toMatch(/^(https?:)?\/\//);
    }
  });
});

describe('OfferCta with an offer id', () => {
  it('links through /go/ instead of opening the demo dialog', () => {
    render(
      <OfferCta
        sponsored={false}
        earnsCommission
        offerType="free_trial"
        demo={false}
        offerId="offer_9"
      />
    );

    expect(screen.getByRole('link', { name: CTA_LABELS.free_trial })).toHaveAttribute(
      'href',
      '/go/offer_9'
    );
    expect(screen.queryByRole('button')).toBeNull();
    expect(screen.queryByText(/demo offer/i)).toBeNull();
  });

  it('keeps the demo dialog for a demo offer even if an id is passed', () => {
    const { container } = render(
      <OfferCta sponsored={false} earnsCommission offerType="free_sample" demo offerId="offer_9" />
    );

    expect(screen.getByRole('button', { name: CTA_LABELS.free_sample })).toBeInTheDocument();
    expect(goAnchors(container)).toEqual([]);
  });

  it('keeps the demo dialog when there is no offer id', () => {
    const { container } = render(
      <OfferCta sponsored={false} earnsCommission offerType="free_sample" demo offerId={null} />
    );

    expect(screen.getByRole('button', { name: CTA_LABELS.free_sample })).toBeInTheDocument();
    expect(goAnchors(container)).toEqual([]);
  });
});

describe('OfferHero', () => {
  const live = OFFERS.find((offer) => offer.status === 'published');
  if (!live) throw new Error('fixtures need a published offer');
  const merchant = MERCHANTS.find((entry) => entry.slug === live.merchantSlug);
  const category = CATEGORIES.find((entry) => entry.slug === live.categorySlug);
  if (!merchant || !category) throw new Error('fixtures need the offer merchant and category');

  it('keeps a demo offer on the demo dialog: a fixture id has no destination', () => {
    const { container } = render(
      <OfferHero
        offer={{ ...live, demo: true }}
        merchant={merchant}
        category={category}
        ended={false}
      />
    );

    expect(goAnchors(container)).toEqual([]);
    expect(screen.getByRole('button', { name: CTA_LABELS[live.offerType] })).toBeInTheDocument();
  });

  it('links a real offer through /go/<its id> with the paid-link rel', () => {
    const { container } = render(
      <OfferHero
        offer={{ ...live, demo: false, source: 'api' }}
        merchant={merchant}
        category={category}
        ended={false}
      />
    );

    const anchors = goAnchors(container);
    expect(anchors).toHaveLength(1);
    expect(anchors[0]).toHaveAttribute('href', `/go/${live.id}`);
    for (const anchor of anchors) {
      expect(anchor).toHaveAttribute('rel', 'sponsored nofollow noopener');
    }
  });

  it('shows the ended panel and no /go/ link once a real offer has ended', () => {
    const { container } = render(
      <OfferHero
        offer={{ ...live, demo: false, source: 'api' }}
        merchant={merchant}
        category={category}
        ended
      />
    );

    expect(screen.getByRole('heading', { name: 'This offer has ended' })).toBeInTheDocument();
    expect(goAnchors(container)).toEqual([]);
  });
});
