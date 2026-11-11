/**
 * FREEJ-60: the "Ad" label and the commission sentence appear only on an offer that earns commission.
 * "Sponsored" is a different disclosure and is unaffected.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

const mocks = vi.hoisted(() => ({ warn: vi.fn() }));
vi.mock('@/lib/logger', () => ({ logger: { warn: mocks.warn } }));

import type { ApiOfferDetail } from '@/lib/api/catalogue';
import { OfferCta } from '@/components/offer/offer-cta';
import { OfferHero } from '@/components/offer/offer-hero';
import { CTA_LABELS } from '@/components/offer/offer-format';
import { AffiliateCta } from '@/components/offer/outbound-link';
import { mapOfferDetail } from '@/lib/catalogue/api-mapper';
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

function comesBefore(first: HTMLElement, second: HTMLElement): boolean {
  return Boolean(first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING);
}

beforeEach(() => vi.clearAllMocks());

describe('an offer that does not earn commission', () => {
  it('shows no Ad label and no commission sentence', () => {
    const { container } = render(
      <AffiliateCta
        offerId="offer_1"
        offerType="free_sample"
        sponsored={false}
        earnsCommission={false}
      />
    );

    expect(screen.queryByText('Ad')).toBeNull();
    expect(container).not.toHaveTextContent(/commission/i);
    expect(container).not.toHaveTextContent(SPONSORED);
  });

  it('still links through /go/ with the paid-link rel, marked "not disclosed" with ?nd=1', () => {
    const { container } = render(
      <AffiliateCta
        offerId="offer_1"
        offerType="free_sample"
        sponsored={false}
        earnsCommission={false}
      />
    );

    const link = screen.getByRole('link', { name: CTA_LABELS.free_sample });
    expect(link).toHaveAttribute('href', '/go/offer_1?nd=1');
    expect(link).toHaveAttribute('rel', 'sponsored nofollow noopener');
    expect(container).toHaveTextContent("This takes you to the brand's website");
  });

  it('leaves no empty paragraph and no aria-describedby on the link', () => {
    const { container } = render(
      <AffiliateCta
        offerId="offer_1"
        offerType="free_sample"
        sponsored={false}
        earnsCommission={false}
      />
    );

    expect(screen.getByRole('link', { name: CTA_LABELS.free_sample })).not.toHaveAttribute(
      'aria-describedby'
    );
    const empty = Array.from(container.querySelectorAll('p')).filter(
      (paragraph) => !paragraph.textContent?.trim()
    );
    expect(empty).toEqual([]);
  });

  it('puts the button first, with no text above it, and spaces the notice as the first line below it', () => {
    const { container } = render(
      <AffiliateCta
        offerId="offer_1"
        offerType="free_sample"
        sponsored={false}
        earnsCommission={false}
      />
    );

    expect(container.textContent?.startsWith(CTA_LABELS.free_sample)).toBe(true);
    const notice = container.firstElementChild!.lastElementChild as HTMLElement;
    expect(notice.className).toContain('mt-3');
    expect(notice.className).not.toContain('mt-1');
  });

  it('keeps the conditions line described by the link; the notice is still spaced as a first muted line', () => {
    const { container } = render(
      <AffiliateCta
        offerId="offer_1"
        offerType="free_sample"
        sponsored={false}
        earnsCommission={false}
        conditions="Postage: free"
      />
    );

    const link = screen.getByRole('link', { name: CTA_LABELS.free_sample });
    expect(link).toHaveAccessibleDescription('Postage: free');
    expect(describedByIds(link)).toHaveLength(1);
    const notice = container.firstElementChild!.lastElementChild as HTMLElement;
    expect(notice.className).toContain('mt-3');
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
    const notice = container.firstElementChild!.lastElementChild as HTMLElement;
    expect(notice.className).toContain('mt-1');
  });

  it.each([true, false])(
    'every id in aria-describedby points at an element that exists (earnsCommission %s)',
    (earnsCommission) => {
      for (const sponsored of [true, false]) {
        for (const conditions of ['Postage: free', null]) {
          const { container, unmount } = render(
            <AffiliateCta
              offerId="offer_1"
              offerType="free_sample"
              sponsored={sponsored}
              earnsCommission={earnsCommission}
              conditions={conditions}
            />
          );
          const link = screen.getByRole('link', { name: CTA_LABELS.free_sample });
          for (const id of describedByIds(link)) {
            expect(container.querySelector(`[id="${id}"]`)).not.toBeNull();
          }
          unmount();
        }
      }
    }
  );
});

describe('an offer that earns commission', () => {
  it('shows the Ad label before the button and the commission sentence, with a plain /go/ link', () => {
    const { container } = render(
      <AffiliateCta offerId="offer_1" offerType="free_sample" sponsored={false} earnsCommission />
    );

    const label = screen.getByText('Ad');
    const link = screen.getByRole('link', { name: CTA_LABELS.free_sample });
    expect(comesBefore(label, link)).toBe(true);
    expect(link).toHaveAttribute('href', '/go/offer_1');
    expect(container).toHaveTextContent(COMMISSION);
    expect(container).not.toHaveTextContent(MAY_EARN);
  });

  it('describes the link by the commission sentence, and puts the notice tight beneath it', () => {
    const { container } = render(
      <AffiliateCta
        offerId="offer_1"
        offerType="free_sample"
        sponsored={false}
        earnsCommission
        conditions="Postage: free"
      />
    );

    const link = screen.getByRole('link', { name: CTA_LABELS.free_sample });
    expect(link).toHaveAccessibleDescription(/Postage: free.*paid a commission/i);
    expect(describedByIds(link)).toHaveLength(2);
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

describe('OfferCta and OfferHero pass the flag through', () => {
  it('OfferCta hides Ad and the sentence for a real offer that does not earn', () => {
    const { container } = render(
      <OfferCta
        offerType="free_sample"
        demo={false}
        offerId="o1"
        sponsored={false}
        earnsCommission={false}
      />
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

  it('OfferHero: an offer that earns shows both, with Ad before the link', () => {
    const { container } = hero({ ...base, sponsored: false, earnsCommission: true });

    expect(
      comesBefore(
        screen.getByText('Ad'),
        screen.getByRole('link', { name: CTA_LABELS[base.offerType] })
      )
    ).toBe(true);
    expect(container).toHaveTextContent(COMMISSION);
  });

  it('OfferHero: an unknown flag (null) discloses, like true', () => {
    const { container } = hero({ ...base, sponsored: false, earnsCommission: null });

    expect(screen.getByText('Ad')).toBeInTheDocument();
    expect(container).toHaveTextContent(COMMISSION);
    expect(screen.getByRole('link', { name: CTA_LABELS[base.offerType] })).toHaveAttribute(
      'href',
      `/go/${base.id}`
    );
  });

  it('OfferHero: a sponsored offer shows Sponsored whatever the flag says, and Ad only when it earns', () => {
    const earning = hero({ ...base, sponsored: true, earnsCommission: true });
    expect(screen.getByRole('note')).toHaveTextContent('Sponsored listing');
    expect(
      comesBefore(
        screen.getByText('Ad'),
        screen.getByRole('link', { name: CTA_LABELS[base.offerType] })
      )
    ).toBe(true);
    expect(earning.container).toHaveTextContent(MAY_EARN);
    earning.unmount();

    const direct = hero({ ...base, sponsored: true, earnsCommission: false });
    expect(screen.getByRole('note')).toHaveTextContent('Sponsored listing');
    expect(screen.queryByText('Ad')).toBeNull();
    expect(direct.container).not.toHaveTextContent(MAY_EARN);
    expect(screen.getByText(SPONSORED)).toBeInTheDocument();
  });

  it('a demo offer keeps its dialog and shows no Ad label whatever the flag says', () => {
    render(
      <OfferCta offerType="free_sample" demo offerId="o1" sponsored={false} earnsCommission />
    );

    expect(screen.queryByText('Ad')).toBeNull();
    expect(screen.getByRole('button', { name: CTA_LABELS.free_sample })).toBeInTheDocument();
  });
});

describe('the demo offer wording', () => {
  it('says that only a link that earns Freejoy a commission is marked Ad, in the dialog and the footnote', async () => {
    const { container } = render(
      <OfferCta offerType="voucher" demo sponsored={false} earnsCommission />
    );

    expect(container).toHaveTextContent(
      'Affiliate disclosure: real offers that earn Freejoy a commission are marked "Ad" and say how Freejoy is paid.'
    );
    screen.getByRole('button', { name: CTA_LABELS.voucher }).click();
    expect(
      await screen.findByText(
        'Real offers will link to the brand through a Freejoy redirect. A link that earns Freejoy a commission is marked "Ad" next to the button.'
      )
    ).toBeInTheDocument();
  });
});

/** A whole detail body from the API, mapped as the offer page maps it, then rendered. */
const detailBody = (patch: Record<string, unknown> = {}): ApiOfferDetail =>
  ({
    id: 'offer-77',
    slug: 'free-tea-sample',
    title: 'Free tea sample',
    summary: 'A taster',
    description: ['One.'],
    category: { slug: base.categorySlug, name: category.name },
    merchant: { slug: base.merchantSlug, name: merchant.name, logo_url: null },
    offer_type: 'free_sample',
    sponsored: false,
    earns_commission: true,
    featured: false,
    image: { tint: 'mint', label: 'cuppa', alt: 'A cup of tea' },
    postage: { kind: 'free', amount_minor: null, currency: null },
    requires_card: false,
    subscription_terms: null,
    eligibility: 'UK',
    availability: 'Now',
    start_at: '2026-10-01T09:00:00Z',
    end_at: null,
    last_verified_at: '2026-10-28T10:00:00Z',
    freshness: null,
    steps: [],
    ...patch,
  }) as ApiOfferDetail;

describe('the offer page from a mapped detail body', () => {
  it('earns_commission true: Ad before the link, the commission sentence and a plain /go/ link', () => {
    const offer = mapOfferDetail(detailBody({ earns_commission: true }))!;

    const { container } = hero(offer);

    const link = screen.getByRole('link', { name: CTA_LABELS.free_sample });
    expect(comesBefore(screen.getByText('Ad'), link)).toBe(true);
    expect(link).toHaveAttribute('href', '/go/offer-77');
    expect(container).toHaveTextContent(COMMISSION);
  });

  it('earns_commission false: no Ad, no commission sentence, and a link marked ?nd=1', () => {
    const offer = mapOfferDetail(detailBody({ earns_commission: false }))!;

    const { container } = hero(offer);

    expect(screen.queryByText('Ad')).toBeNull();
    expect(container).not.toHaveTextContent(/commission/i);
    expect(screen.getByRole('link', { name: CTA_LABELS.free_sample })).toHaveAttribute(
      'href',
      '/go/offer-77?nd=1'
    );
  });

  it('earns_commission missing: the page discloses, and the mapper warned once', () => {
    const { earns_commission: _omitted, ...body } = detailBody();
    const offer = mapOfferDetail(body as ApiOfferDetail)!;

    const { container } = hero(offer);

    expect(screen.getByText('Ad')).toBeInTheDocument();
    expect(container).toHaveTextContent(COMMISSION);
    expect(mocks.warn).toHaveBeenCalledTimes(1);
  });
});
