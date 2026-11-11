/**
 * FREEJ-60: the "Ad" label and the commission sentence appear only on a link that earns commission.
 * "Sponsored" is a different disclosure and is unaffected.
 */
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { OfferCta } from '@/components/offer/offer-cta';
import { OfferHero } from '@/components/offer/offer-hero';
import { CTA_LABELS } from '@/components/offer/offer-format';
import { AffiliateCta } from '@/components/offer/outbound-link';
import { CATEGORIES, MERCHANTS, OFFERS } from '@/lib/catalogue/fixtures';
import type { Offer } from '@/lib/catalogue/types';

const COMMISSION = /is paid a commission by the brand/i;
const MAY_EARN = /may also earn a commission/i;
const SPONSORED = /sponsored listing: the brand pays Freejoy/i;

const base: Offer = { ...OFFERS[0], demo: false, source: 'api' };
const merchant = MERCHANTS.find((item) => item.slug === base.merchantSlug)!;
const category = CATEGORIES.find((item) => item.slug === base.categorySlug)!;

function hero(offer: Offer) {
  return render(<OfferHero offer={offer} merchant={merchant} category={category} ended={false} />);
}

function describedByIds(link: HTMLElement): string[] {
  return (link.getAttribute('aria-describedby') ?? '').split(' ').filter(Boolean);
}

describe('a link that does not earn commission (direct only)', () => {
  it('shows no Ad label and no commission sentence', () => {
    const { container } = render(
      <AffiliateCta offerId="offer_1" offerType="free_sample" earnsCommission={false} />
    );

    expect(screen.queryByText('Ad')).toBeNull();
    expect(container).not.toHaveTextContent(COMMISSION);
    expect(container).not.toHaveTextContent(/commission/i);
    expect(container).not.toHaveTextContent(SPONSORED);
  });

  it('still links through /go/ with the paid-link rel and the redirect notice', () => {
    const { container } = render(
      <AffiliateCta offerId="offer_1" offerType="free_sample" earnsCommission={false} />
    );

    const link = screen.getByRole('link', { name: CTA_LABELS.free_sample });
    expect(link).toHaveAttribute('href', '/go/offer_1');
    expect(link).toHaveAttribute('rel', 'sponsored nofollow noopener');
    expect(container).toHaveTextContent("This takes you to the brand's website");
  });

  it('leaves no empty paragraph and no aria-describedby on the link', () => {
    const { container } = render(
      <AffiliateCta offerId="offer_1" offerType="free_sample" earnsCommission={false} />
    );

    const link = screen.getByRole('link', { name: CTA_LABELS.free_sample });
    expect(link).not.toHaveAttribute('aria-describedby');
    const empty = Array.from(container.querySelectorAll('p')).filter(
      (paragraph) => !paragraph.textContent?.trim()
    );
    expect(empty).toEqual([]);
  });

  it('puts the button first, with no label above it and no orphan spacing before the notice', () => {
    const { container } = render(
      <AffiliateCta offerId="offer_1" offerType="free_sample" earnsCommission={false} />
    );

    const wrapper = container.firstElementChild!;
    expect(wrapper.firstElementChild?.tagName).toBe('A');
    const notice = wrapper.lastElementChild as HTMLElement;
    expect(notice.className).toContain('mt-3');
    expect(notice.className).not.toContain('mt-1');
  });

  it('keeps the conditions line described by the link, and the notice tight beneath it', () => {
    const { container } = render(
      <AffiliateCta
        offerId="offer_1"
        offerType="free_sample"
        earnsCommission={false}
        conditions="Postage: free"
      />
    );

    const link = screen.getByRole('link', { name: CTA_LABELS.free_sample });
    expect(link).toHaveAccessibleDescription('Postage: free');
    expect(describedByIds(link)).toHaveLength(1);
    expect((container.firstElementChild!.lastElementChild as HTMLElement).className).toContain(
      'mt-1'
    );
  });

  it('keeps Sponsored in every case: a sponsored direct-only offer says the brand pays, with no Ad and no commission', () => {
    const { container } = render(
      <AffiliateCta offerId="offer_1" offerType="free_sample" sponsored earnsCommission={false} />
    );

    expect(screen.queryByText('Ad')).toBeNull();
    expect(screen.getByText(SPONSORED)).toBeInTheDocument();
    expect(container).not.toHaveTextContent(/commission/i);
    expect(screen.getByRole('link', { name: CTA_LABELS.free_sample })).toHaveAccessibleDescription(
      SPONSORED
    );
  });

  it('every id in aria-describedby points at an element that exists', () => {
    for (const sponsored of [true, false]) {
      for (const conditions of ['Postage: free', null]) {
        const { container, unmount } = render(
          <AffiliateCta
            offerId="offer_1"
            offerType="free_sample"
            sponsored={sponsored}
            conditions={conditions}
            earnsCommission={false}
          />
        );
        const link = screen.getByRole('link', { name: CTA_LABELS.free_sample });
        for (const id of describedByIds(link)) {
          expect(container.querySelector(`[id="${id}"]`)).not.toBeNull();
        }
        unmount();
      }
    }
  });
});

describe('a link that earns commission (affiliate network)', () => {
  it('shows the Ad label before the button and the commission sentence', () => {
    const { container } = render(
      <AffiliateCta offerId="offer_1" offerType="free_sample" earnsCommission />
    );

    const label = screen.getByText('Ad');
    const link = screen.getByRole('link', { name: CTA_LABELS.free_sample });
    expect(label.compareDocumentPosition(link) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(container).toHaveTextContent(COMMISSION);
    expect(container).not.toHaveTextContent(MAY_EARN);
  });

  it('puts the notice tight beneath the commission sentence', () => {
    const { container } = render(
      <AffiliateCta offerId="offer_1" offerType="free_sample" earnsCommission />
    );

    expect((container.firstElementChild!.lastElementChild as HTMLElement).className).toContain(
      'mt-1'
    );
  });

  it('shows Sponsored and Ad together, and says the commission may also be earned', () => {
    const { container } = render(
      <AffiliateCta offerId="offer_1" offerType="free_sample" sponsored earnsCommission />
    );

    expect(screen.getByText('Ad')).toBeInTheDocument();
    expect(screen.getByText(SPONSORED)).toBeInTheDocument();
    expect(screen.getByText(MAY_EARN)).toBeInTheDocument();
    expect(container).not.toHaveTextContent(COMMISSION);
  });
});

describe('the unknown case discloses (over-disclosure is the safe side)', () => {
  it('AffiliateCta shows Ad and the commission sentence when earnsCommission is not passed', () => {
    const { container } = render(<AffiliateCta offerId="offer_1" offerType="free_sample" />);

    expect(screen.getByText('Ad')).toBeInTheDocument();
    expect(container).toHaveTextContent(COMMISSION);
  });

  it('OfferCta shows Ad and the commission sentence when earnsCommission is not passed', () => {
    const { container } = render(<OfferCta offerType="free_sample" demo={false} offerId="o1" />);

    expect(screen.getByText('Ad')).toBeInTheDocument();
    expect(container).toHaveTextContent(COMMISSION);
  });
});

describe('OfferCta and OfferHero pass the flag through', () => {
  it('OfferCta hides Ad and the sentence for a real offer that does not earn', () => {
    const { container } = render(
      <OfferCta offerType="free_sample" demo={false} offerId="o1" earnsCommission={false} />
    );

    expect(screen.queryByText('Ad')).toBeNull();
    expect(container).not.toHaveTextContent(/commission/i);
  });

  it('OfferHero: a direct-only, unsponsored offer shows neither label nor sentence', () => {
    const { container } = hero({ ...base, sponsored: false, earnsCommission: false });

    expect(screen.queryByText('Ad')).toBeNull();
    expect(screen.queryByRole('note')).toBeNull();
    expect(container).not.toHaveTextContent(/commission/i);
    expect(screen.getByRole('link', { name: CTA_LABELS[base.offerType] })).toBeInTheDocument();
  });

  it('OfferHero: an offer on an affiliate network shows both', () => {
    const { container } = hero({ ...base, sponsored: false, earnsCommission: true });

    expect(screen.getByText('Ad')).toBeInTheDocument();
    expect(container).toHaveTextContent(COMMISSION);
  });

  it('OfferHero: a sponsored offer shows Sponsored whatever the flag says, and Ad only when it earns', () => {
    const earning = hero({ ...base, sponsored: true, earnsCommission: true });
    expect(screen.getByRole('note')).toHaveTextContent('Sponsored listing');
    expect(screen.getByText('Ad')).toBeInTheDocument();
    expect(earning.container).toHaveTextContent(MAY_EARN);
    earning.unmount();

    const direct = hero({ ...base, sponsored: true, earnsCommission: false });
    expect(screen.getByRole('note')).toHaveTextContent('Sponsored listing');
    expect(screen.queryByText('Ad')).toBeNull();
    expect(direct.container).not.toHaveTextContent(MAY_EARN);
    expect(screen.getByText(SPONSORED)).toBeInTheDocument();
  });

  it('a demo offer keeps its dialog and shows no Ad label whatever the flag says', () => {
    render(<OfferCta offerType="free_sample" demo offerId="o1" earnsCommission />);

    expect(screen.queryByText('Ad')).toBeNull();
    expect(screen.getByRole('button', { name: CTA_LABELS.free_sample })).toBeInTheDocument();
  });
});
