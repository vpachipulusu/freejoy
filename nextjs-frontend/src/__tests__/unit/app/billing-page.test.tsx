import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { setupUser } from '@/__tests__/unit/helpers/user';
import { findIdleControl } from '@/__tests__/unit/helpers/idle';

const mocks = vi.hoisted(() => ({
  getSubscriptionDetailsAction: vi.fn(),
  getPaymentMethodsAction: vi.fn(),
  getSubscriptionInvoicesAction: vi.fn(),
  cancelSubscriptionAction: vi.fn(),
  resumeSubscriptionAction: vi.fn(),
  deletePaymentMethodAction: vi.fn(),
  setDefaultPaymentMethodAction: vi.fn(),
  createBillingPortalSessionAction: vi.fn(),
  toast: vi.fn(),
}));

vi.mock('@/app/actions/subscription-actions', () => ({
  getSubscriptionDetailsAction: mocks.getSubscriptionDetailsAction,
  getPaymentMethodsAction: mocks.getPaymentMethodsAction,
  getSubscriptionInvoicesAction: mocks.getSubscriptionInvoicesAction,
  cancelSubscriptionAction: mocks.cancelSubscriptionAction,
  resumeSubscriptionAction: mocks.resumeSubscriptionAction,
  deletePaymentMethodAction: mocks.deletePaymentMethodAction,
  setDefaultPaymentMethodAction: mocks.setDefaultPaymentMethodAction,
  createBillingPortalSessionAction: mocks.createBillingPortalSessionAction,
}));
vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: mocks.toast }) }));

import BillingPage from '@/app/dashboard/settings/billing/page';

const PRO_ACTIVE = {
  subscriptionTier: 'pro',
  subscriptionStatus: 'active',
  currentPeriodEnd: '2026-12-01T00:00:00Z',
  hasStripeCustomer: true,
  billingPeriod: 'monthly',
  autoRenewal: true,
};

const FREE = {
  subscriptionTier: 'free',
  subscriptionStatus: 'inactive',
  currentPeriodEnd: null,
  hasStripeCustomer: false,
  billingPeriod: null,
  autoRenewal: false,
};

const INVOICES = [
  {
    id: 'in_1',
    amount: 2900,
    currency: 'usd',
    status: 'paid',
    created: '2026-11-01T00:00:00Z',
    pdfUrl: 'https://example.test/in_1.pdf',
    hostedUrl: 'https://example.test/in_1',
  },
  {
    id: 'in_2',
    amount: 2900,
    currency: 'usd',
    status: 'open',
    created: '2026-12-01T00:00:00Z',
    pdfUrl: null,
    hostedUrl: null,
  },
];

function givenBilling({
  subscription = PRO_ACTIVE,
  paymentMethods = [],
  invoices = [],
}: {
  subscription?: Record<string, unknown>;
  paymentMethods?: unknown[];
  invoices?: unknown[];
} = {}) {
  mocks.getSubscriptionDetailsAction.mockResolvedValue({ success: true, data: subscription });
  mocks.getPaymentMethodsAction.mockResolvedValue({ success: true, data: { paymentMethods } });
  mocks.getSubscriptionInvoicesAction.mockResolvedValue({ success: true, data: { invoices } });
}

async function renderPage() {
  render(<BillingPage />);
  await screen.findByRole('heading', { name: 'Billing & Subscription' });
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

describe('BillingPage subscription pills', () => {
  it('shows an Active pill for an active subscription', async () => {
    givenBilling();
    await renderPage();

    const plan = screen.getByRole('group', { name: 'Current plan' });
    expect(within(plan).getByText('Active')).toBeInTheDocument();
    expect(within(plan).getByText('Billed monthly')).toBeInTheDocument();
  });

  it('shows a critical pill with text for a past-due subscription', async () => {
    givenBilling({ subscription: { ...PRO_ACTIVE, subscriptionStatus: 'past_due' } });
    await renderPage();

    expect(
      within(screen.getByRole('group', { name: 'Current plan' })).getByText('Payment past due')
    ).toBeInTheDocument();
  });

  it('shows a neutral pill for the free plan', async () => {
    givenBilling({ subscription: FREE });
    await renderPage();

    expect(
      within(screen.getByRole('group', { name: 'Current plan' })).getByText('Inactive')
    ).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Upgrade Plan' })).toHaveAttribute('href', '/pricing');
  });
});

describe('BillingPage invoice table', () => {
  it('renders invoices in a table with status pills and document links', async () => {
    givenBilling({ invoices: INVOICES });
    await renderPage();

    const table = screen.getByRole('table', { name: 'Invoices' });
    const rows = within(table).getAllByRole('row');
    expect(rows).toHaveLength(3);
    expect(within(rows[1]).getByText('Paid')).toBeInTheDocument();
    expect(within(rows[1]).getByText('US$29.00')).toBeInTheDocument();
    expect(within(rows[1]).getByRole('link', { name: /PDF for invoice/ })).toHaveAttribute(
      'href',
      'https://example.test/in_1.pdf'
    );
    expect(within(rows[2]).getByText('Open')).toBeInTheDocument();
    expect(within(rows[2]).queryByRole('link')).not.toBeInTheDocument();
  });

  it('shows the empty state when there are no invoices', async () => {
    givenBilling();
    await renderPage();

    expect(screen.getByText('No invoices yet')).toBeInTheDocument();
    expect(screen.queryByRole('table', { name: 'Invoices' })).not.toBeInTheDocument();
  });
});

describe('BillingPage plan usage', () => {
  it('shows meters from the typed fixture for the current tier and says they are samples', async () => {
    givenBilling({ subscription: FREE });
    await renderPage();

    const usage = screen.getByRole('group', { name: 'Plan usage' });
    expect(within(usage).getAllByRole('meter').length).toBeGreaterThan(0);
    expect(screen.getByText(/sample figures/i)).toBeInTheDocument();
  });
});

describe('BillingPage inline errors', () => {
  it('shows a role=alert message when billing data fails to load', async () => {
    mocks.getSubscriptionDetailsAction.mockRejectedValue(new Error('boom'));
    mocks.getPaymentMethodsAction.mockResolvedValue({
      success: true,
      data: { paymentMethods: [] },
    });
    mocks.getSubscriptionInvoicesAction.mockResolvedValue({
      success: true,
      data: { invoices: [] },
    });

    await renderPage();

    expect(screen.getByRole('alert')).toHaveTextContent('Failed to load billing data');
  });

  it('shows the backend message inline when an action fails, and clears it on success', async () => {
    const user = setupUser();
    givenBilling();
    mocks.cancelSubscriptionAction.mockResolvedValueOnce({
      success: false,
      error: 'Stripe said no',
    });
    mocks.cancelSubscriptionAction.mockResolvedValueOnce({
      success: true,
      data: { message: 'Done' },
    });
    await renderPage();

    await user.click(screen.getByRole('switch', { name: 'Auto-renewal' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Stripe said no');

    await user.click(await findIdleControl('switch', 'Auto-renewal'));
    await vi.waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
  });

  it('shows the backend message inline when one of the loads reports failure', async () => {
    givenBilling();
    mocks.getSubscriptionInvoicesAction.mockResolvedValue({
      success: false,
      error: 'Invoices unavailable',
    });

    await renderPage();

    expect(screen.getByRole('alert')).toHaveTextContent('Invoices unavailable');
  });

  it('shows an inline alert when the billing portal cannot be opened', async () => {
    const user = setupUser();
    givenBilling();
    mocks.createBillingPortalSessionAction.mockResolvedValue({
      success: false,
      error: 'Portal unavailable',
    });
    await renderPage();

    await user.click(screen.getByRole('button', { name: 'Add Payment Method' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Portal unavailable');
  });
});

describe('BillingPage actions are unchanged', () => {
  it('turns auto-renewal off through cancelSubscriptionAction', async () => {
    const user = setupUser();
    givenBilling();
    mocks.cancelSubscriptionAction.mockResolvedValue({ success: true, data: { message: 'Done' } });
    await renderPage();

    await user.click(screen.getByRole('switch', { name: 'Auto-renewal' }));

    expect(mocks.cancelSubscriptionAction).toHaveBeenCalledTimes(1);
    expect(mocks.resumeSubscriptionAction).not.toHaveBeenCalled();
  });

  it('sets a non-default card as default and removes a card', async () => {
    const user = setupUser();
    givenBilling({
      paymentMethods: [
        { id: 'pm_1', brand: 'visa', last4: '4242', expMonth: 4, expYear: 2030, isDefault: true },
        {
          id: 'pm_2',
          brand: 'mastercard',
          last4: '1111',
          expMonth: 1,
          expYear: 2031,
          isDefault: false,
        },
      ],
    });
    mocks.setDefaultPaymentMethodAction.mockResolvedValue({ success: true, data: {} });
    mocks.deletePaymentMethodAction.mockResolvedValue({ success: true, data: {} });
    await renderPage();

    expect(screen.getByText('Default')).toBeInTheDocument();
    await user.click(
      screen.getByRole('button', { name: /set mastercard ending in 1111 as default/i })
    );
    expect(mocks.setDefaultPaymentMethodAction).toHaveBeenCalledWith('pm_2');

    await user.click(screen.getAllByRole('button', { name: /remove/i })[1]);
    expect(mocks.deletePaymentMethodAction).toHaveBeenCalledWith('pm_2');
  });
});
