import { describe, expect, it } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { setupUser } from '@/__tests__/unit/helpers/user';
import { OfferCta } from '@/components/offer/offer-cta';
import { OfferHero } from '@/components/offer/offer-hero';
import { CATEGORIES, OFFERS } from '@/lib/catalogue/fixtures';

describe('OfferCta', () => {
  it('opens the demo offer dialog when the call to action is clicked', async () => {
    const user = setupUser();
    render(<OfferCta sponsored={false} earnsCommission offerType="free_sample" demo />);

    await user.click(screen.getByRole('button', { name: /request this sample/i }));

    const dialog = await screen.findByRole('dialog', { name: 'Demo offer' });
    expect(dialog).toHaveTextContent(/design preview with sample data/i);
    expect(dialog).toHaveTextContent(/nothing was clicked through/i);
  });

  it('closes on Escape and returns focus to the call to action', async () => {
    const user = setupUser();
    render(<OfferCta sponsored={false} earnsCommission offerType="free_trial" demo />);
    const trigger = screen.getByRole('button', { name: /start the trial/i });

    await user.click(trigger);
    await screen.findByRole('dialog', { name: 'Demo offer' });
    await user.keyboard('{Escape}');

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it('labels the button by offer type and never links to an external site', () => {
    render(<OfferCta sponsored={false} earnsCommission offerType="competition" demo />);

    expect(screen.getByRole('button', { name: /enter this competition/i })).toBeInTheDocument();
    expect(screen.getByText(/to request your entry/i)).toBeInTheDocument();
    for (const link of screen.getAllByRole('link')) {
      expect(link.getAttribute('href') ?? '').not.toMatch(/^https?:/);
    }
    expect(screen.getByRole('link', { name: /affiliate disclosure/i })).toHaveAttribute(
      'href',
      '/affiliate-disclosure'
    );
  });

  it('has no ended state of its own: the hero renders the ended panel instead', () => {
    render(
      <OfferHero
        offer={{ ...OFFERS[0], demo: true }}
        merchant={{ slug: 'm', name: 'M', logoUrl: null }}
        category={{ ...CATEGORIES[0], slug: 'home-cleaning' }}
        ended
      />
    );

    expect(screen.getByRole('heading', { name: 'This offer has ended' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /get this|start the|enter this/i })).toBeNull();
    expect(screen.getByRole('link', { name: /more in this category/i })).toHaveAttribute(
      'href',
      '/category/home-cleaning'
    );
    expect(screen.getByRole('link', { name: /browse all freebies/i })).toHaveAttribute(
      'href',
      '/freebies'
    );
  });
});
