import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

// FREEJ-30: public copy must match what the site does today (docs/compliance/uk-affiliate-checklist.md,
// section I.2). These pin the wording the compliance review flagged so it cannot creep back.

vi.mock('next/navigation', () => ({
  usePathname: () => '/',
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
}));

import { AnnouncementBar } from '@/components/site/announcement-bar';
import { SiteFooter } from '@/components/site/site-footer';
import { HomeHero } from '@/components/home/home-hero';
import { TrustStrip } from '@/components/home/trust-strip';
import { NewsletterBand } from '@/components/home/newsletter-band';
import { CatalogueHeader } from '@/components/catalogue/catalogue-header';
import { OfferCta } from '@/components/offer/offer-cta';
import { OfferTrustPanel } from '@/components/offer/offer-trust-panel';
import { siteConfig } from '@/config/site';
import { DEFAULT_QUERY } from '@/lib/catalogue/offer-url';
import { OFFERS } from '@/lib/catalogue/fixtures';

// "genuine" is listed because the old description said "genuine UK free samples", which implies a
// verification the site cannot evidence (CAP 3.7).
const ABSOLUTES = /zero spend|for £0|no spend|genuine|daily dose|hidden surprises|freebies only/i;
const SAMPLE = OFFERS.find((offer) => offer.offerType === 'free_sample');
if (!SAMPLE) throw new Error('The fixtures must contain a free_sample offer');

describe('site-wide slogans', () => {
  it('announcement bar does not say everything costs nothing or refreshes daily', () => {
    const { container } = render(<AnnouncementBar />);

    expect(container.textContent).not.toMatch(ABSOLUTES);
    expect(container.textContent).not.toMatch(/daily|£0/i);
    expect(container.textContent).toMatch(/terms/i);
  });

  it('the tagline and description (footer, metadata, manifest) make no zero-cost or quality claim', () => {
    expect(siteConfig.tagline).not.toMatch(ABSOLUTES);
    expect(siteConfig.description).not.toMatch(ABSOLUTES);
    const { container } = render(<SiteFooter />);
    expect(container).toHaveTextContent(siteConfig.tagline);
  });

  it.each([true, false])('the home hero makes no zero-cost or quality claim (demo=%s)', (demo) => {
    const { container } = render(<HomeHero demo={demo} />);

    expect(container.textContent).not.toMatch(ABSOLUTES);
  });

  it('the trust strip describes every offer type and makes no absolute promise', () => {
    render(<TrustStrip demo={false} />);

    const strip = screen.getByRole('region', { name: /how we list offers/i });
    expect(strip.textContent).not.toMatch(ABSOLUTES);
    expect(strip.textContent).toMatch(/cashback/i);
    expect(strip.textContent).toMatch(/vouchers/i);
  });

  it('the catalogue header does not call every listing free', () => {
    const { container } = render(<CatalogueHeader query={DEFAULT_QUERY} demo={false} />);

    expect(container.textContent).not.toMatch(/free thing/i);
  });

  // FREEJ-42: the backend matches the search word against offer title, summary and brand name only
  // (_search_condition in fastapi_backend/app/repositories/catalogue.py), never against categories.
  it('the catalogue search placeholder names only what the search matches', () => {
    render(<CatalogueHeader query={DEFAULT_QUERY} demo={false} />);

    const box = screen.getByPlaceholderText('Search offers or brands');
    expect(box).toBeInTheDocument();
    expect(box.getAttribute('placeholder')).not.toMatch(/categor/i);
  });
});

describe('alerts promises on the home page', () => {
  it('promises no cadence, no "no spam" and no "anytime", and says how to stop', () => {
    const { container } = render(<NewsletterBand />);

    expect(container.textContent).not.toMatch(/regular|no spam|anytime|good stuff/i);
    expect(container).toHaveTextContent('Alerts about offers in the categories you choose.');
    expect(container).toHaveTextContent('Only the alerts you choose. Unsubscribe from any email.');
    expect(screen.getByRole('link', { name: 'privacy details' })).toHaveAttribute(
      'href',
      '/privacy'
    );
  });
});

describe('offer trust panel date', () => {
  it('calls a demo date a sample date and never says it was checked', () => {
    const { container } = render(<OfferTrustPanel offer={{ ...SAMPLE, demo: true }} />);

    expect(screen.getByText(/^Sample date \d/)).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/last checked/i);
    expect(container.textContent).toMatch(/has not checked this offer/i);
    expect(screen.getByRole('region', { name: 'Offer details' })).toBeInTheDocument();
  });

  it('shows "Last checked" for a real offer only', () => {
    const { container } = render(
      <OfferTrustPanel offer={{ ...SAMPLE, demo: false, source: 'api' }} />
    );

    expect(screen.getByText(/^Last checked \d/)).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/sample date|has not checked/i);
    expect(screen.getByRole('region', { name: 'Offer checks' })).toBeInTheDocument();
  });
});

describe('call to action conditions line', () => {
  it('shows the generated line after a demo button', () => {
    render(
      <OfferCta
        sponsored={false}
        earnsCommission
        offerType="free_sample"
        demo
        conditions="Postage: £2.99."
      />
    );

    expect(screen.getByText('Postage: £2.99.')).toBeInTheDocument();
  });

  it('ties the line to the demo button as its accessible description, without renaming the button', () => {
    render(
      <OfferCta
        sponsored={false}
        earnsCommission
        offerType="free_sample"
        demo
        conditions="Postage: £2.99."
      />
    );

    const button = screen.getByRole('button', { name: 'Request this sample' });
    expect(button).toHaveAccessibleDescription('Postage: £2.99.');
    expect(button).not.toHaveAttribute('aria-label');
  });

  it('ties the line to the real offer link as its accessible description, without renaming the link', () => {
    render(
      <OfferCta
        sponsored={false}
        earnsCommission
        offerType="free_sample"
        demo={false}
        offerId="api-1"
        conditions="Postage: £2.99."
      />
    );

    const link = screen.getByRole('link', { name: 'Request this sample' });
    // The commission sentence of an offer that earns is described too (FREEJ-60), after the conditions line.
    expect(link).toHaveAccessibleDescription(/^Postage: £2\.99\. Freejoy is paid a commission/);
    expect(link).not.toHaveAttribute('aria-label');
  });

  it('sets no description when there is no line', () => {
    render(<OfferCta sponsored={false} earnsCommission offerType="cashback" demo />);

    expect(screen.getByRole('button', { name: 'Get this cashback' })).not.toHaveAttribute(
      'aria-describedby'
    );
  });

  it('shows the generated line after a real offer button, before the commission sentence', () => {
    const { container } = render(
      <OfferCta
        sponsored={false}
        earnsCommission
        offerType="free_trial"
        demo={false}
        offerId="api-1"
        conditions="A payment card is required to start."
      />
    );

    const text = container.textContent ?? '';
    expect(text.indexOf('Start the trial')).toBeLessThan(
      text.indexOf('A payment card is required')
    );
    expect(text.indexOf('A payment card is required')).toBeLessThan(
      text.indexOf('Freejoy is paid')
    );
  });

  it('adds nothing when there is no line', () => {
    const { container } = render(
      <OfferCta sponsored={false} earnsCommission offerType="cashback" demo />
    );

    expect(container.textContent).not.toMatch(/postage:|payment card/i);
  });

  it('the demo disclosure line no longer hedges about earning commission "if you qualify"', () => {
    const { container } = render(
      <OfferCta sponsored={false} earnsCommission offerType="voucher" demo />
    );

    expect(container.textContent).not.toMatch(/may earn|if you qualify/i);
    expect(container.textContent).toMatch(/marked "Ad"/);
  });
});
