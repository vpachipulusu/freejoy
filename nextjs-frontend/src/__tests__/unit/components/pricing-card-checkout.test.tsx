import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { findIdleControl } from '@/__tests__/unit/helpers/idle';

/** a failed checkout shows an inline error (no window.alert) and re-enables the button. */

const mocks = vi.hoisted(() => ({ createCheckoutSessionAction: vi.fn() }));
vi.mock('@/app/actions/subscription-actions', () => ({
  createCheckoutSessionAction: mocks.createCheckoutSessionAction,
}));
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));

import { PricingCard } from '@/components/pricing/pricing-card';

const PRO = {
  name: 'Pro',
  price: 15,
  description: 'For professionals',
  features: [],
  limitations: [],
  cta: 'Upgrade',
  href: '/signup',
  popular: true,
};

function renderPro(billingPeriod: 'monthly' | 'yearly' = 'monthly') {
  render(
    <PricingCard
      tier={PRO}
      currencySymbol="$"
      isAuthenticated
      userTier="free"
      billingPeriod={billingPeriod}
    />
  );
  return screen.getByRole('button', { name: /upgrade to pro/i });
}

describe('PricingCard checkout', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(window, 'alert').mockImplementation(() => undefined);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('shows the action message inline, never window.alert, and re-enables the button', async () => {
    mocks.createCheckoutSessionAction.mockResolvedValue({
      success: false,
      error: 'Create an organisation first to subscribe.',
      code: 'no_organization',
    });
    const button = renderPro();

    await userEvent.click(button);

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Create an organisation first to subscribe.'
    );
    expect(window.alert).not.toHaveBeenCalled();
    const retry = screen.getByRole('button', { name: /upgrade to pro/i });
    expect(retry).toBeEnabled();
    expect(retry).toHaveAttribute('aria-describedby', screen.getByRole('alert').id);
  });

  it('keeps the live region mounted before any error', () => {
    const { container } = render(
      <PricingCard tier={PRO} currencySymbol="$" isAuthenticated userTier="free" />
    );

    expect(container.querySelector('[aria-live="assertive"]')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('falls back to a generic inline message when the action throws', async () => {
    mocks.createCheckoutSessionAction.mockRejectedValue(new Error('network'));
    const button = renderPro();

    await userEvent.click(button);

    expect(await screen.findByRole('alert')).toHaveTextContent(/could not start checkout/i);
    expect(window.alert).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: /upgrade to pro/i })).toBeEnabled();
  });

  it('clears the previous error when the user tries again', async () => {
    mocks.createCheckoutSessionAction
      .mockResolvedValueOnce({ success: false, error: 'First failure.' })
      .mockReturnValueOnce(new Promise(() => undefined));
    const button = renderPro();

    await userEvent.click(button);
    await screen.findByRole('alert');
    await userEvent.click(await findIdleControl('button', /upgrade to pro/i));

    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
    expect(screen.getByRole('button', { name: /processing/i })).toBeDisabled();
  });

  it.each(['monthly', 'yearly'] as const)(
    'asks for %s checkout of the pro plan',
    async (period) => {
      mocks.createCheckoutSessionAction.mockReturnValue(new Promise(() => undefined));
      const button = renderPro(period);

      await userEvent.click(button);

      expect(mocks.createCheckoutSessionAction).toHaveBeenCalledWith('pro', period);
    }
  );

  it('uses a generic inline message when the action returns no error text', async () => {
    mocks.createCheckoutSessionAction.mockResolvedValue({ success: false });
    const button = renderPro();

    await userEvent.click(button);

    expect(await screen.findByRole('alert')).toHaveTextContent(/could not start checkout/i);
  });

  it('redirects to Stripe and stays busy while the browser navigates', async () => {
    const location = { href: 'https://app.test/pricing' };
    vi.stubGlobal('location', location);
    mocks.createCheckoutSessionAction.mockResolvedValue({
      success: true,
      data: { url: 'https://checkout.stripe.com/c/pay/cs_test' },
    });
    const button = renderPro();

    await userEvent.click(button);

    await waitFor(() => expect(location.href).toBe('https://checkout.stripe.com/c/pay/cs_test'));
    expect(screen.getByRole('button', { name: /processing/i })).toBeDisabled();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  describe('cards that never start a checkout', () => {
    const FREE = { ...PRO, name: 'Free', price: 0, cta: 'Get started', href: '/signup' };
    const ENTERPRISE = {
      ...PRO,
      name: 'Enterprise',
      price: 90,
      cta: 'Contact Sales',
      href: '/contact',
    };

    it.each([
      ['free card, signed in', FREE, true, 'pro'],
      ['free card, signed out', FREE, false, undefined],
      ['enterprise card, signed in', ENTERPRISE, true, 'free'],
      ['enterprise card, signed out', ENTERPRISE, false, undefined],
      ['pro card, signed out', PRO, false, undefined],
    ])(
      '%s does not call the checkout action or show an error',
      async (_name, tier, isAuthenticated, userTier) => {
        render(
          <PricingCard
            tier={tier}
            currencySymbol="$"
            isAuthenticated={isAuthenticated}
            userTier={userTier}
          />
        );

        const target = screen.getByRole('link');
        target.addEventListener('click', (event) => event.preventDefault());
        await userEvent.click(target);

        expect(mocks.createCheckoutSessionAction).not.toHaveBeenCalled();
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
      }
    );

    it.each(['pro', 'enterprise'])('pro card for a %s subscriber is disabled', async (userTier) => {
      render(<PricingCard tier={PRO} currencySymbol="$" isAuthenticated userTier={userTier} />);

      const button = screen.getByRole('button');
      await userEvent.click(button);

      expect(button).toBeDisabled();
      expect(mocks.createCheckoutSessionAction).not.toHaveBeenCalled();
    });
  });
});
