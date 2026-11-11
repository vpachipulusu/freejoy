/**
 * the Stripe reconciliation panel runs the sync action and lists what differs; failures show the
 * action's curated message.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { setupUser } from '@/__tests__/unit/helpers/user';
import { findIdleControl } from '@/__tests__/unit/helpers/idle';

const mocks = vi.hoisted(() => ({ sync: vi.fn() }));
vi.mock('@/app/actions/admin/analytics-actions', () => ({
  syncStripeSubscriptionsAction: mocks.sync,
}));

import { SyncPanel } from '@/app/(admin)/admin/subscriptions/sync-panel';

const REPORT = {
  stripe_subscriptions: 5,
  in_sync: 3,
  drift_count: 3,
  historical_ignored: 1,
  truncated: false,
  skipped_rows: 0,
  drifts: [
    {
      kind: 'status',
      organization_id: 'org1',
      subscription_id: 'sub_1',
      stripe_status: 'past_due',
      local_status: 'active',
    },
    {
      kind: 'missing_in_stripe',
      organization_id: 'org2',
      subscription_id: 'sub_2',
      stripe_status: null,
      local_status: 'active',
    },
  ],
};

describe('SyncPanel', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shows nothing until the check is run', () => {
    render(<SyncPanel />);

    expect(screen.getByRole('button', { name: /check against stripe/i })).toBeEnabled();
    expect(screen.queryByText(/in sync/i)).not.toBeInTheDocument();
  });

  it('runs the sync and lists the differences', async () => {
    mocks.sync.mockResolvedValue({ success: true, data: REPORT });
    render(<SyncPanel />);

    await setupUser().click(screen.getByRole('button', { name: /check against stripe/i }));

    expect(await screen.findByText(/Read 5 Stripe subscriptions: 3 in sync/)).toBeInTheDocument();
    expect(screen.getByText(/1 old ones ignored/)).toBeInTheDocument();
    expect(screen.getByText('Status differs')).toBeInTheDocument();
    expect(screen.getByText('Not found in Stripe')).toBeInTheDocument();
    expect(screen.getByText(/Showing the first 2 of 3/)).toBeInTheDocument();
    expect(mocks.sync).toHaveBeenCalledTimes(1);
  });

  it('says the result is partial when Stripe has more than one check reads', async () => {
    mocks.sync.mockResolvedValue({
      success: true,
      data: { ...REPORT, drift_count: 0, drifts: [], historical_ignored: 0, truncated: true },
    });
    render(<SyncPanel />);

    await setupUser().click(screen.getByRole('button', { name: /check against stripe/i }));

    expect(await screen.findByText(/result is partial/)).toBeInTheDocument();
  });

  it('shows the action error as an alert and no report', async () => {
    mocks.sync.mockResolvedValue({
      success: false,
      error: 'Something went wrong. Please try again.',
    });
    render(<SyncPanel />);

    await setupUser().click(screen.getByRole('button', { name: /check against stripe/i }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Something went wrong');
    expect(screen.queryByText(/Stripe subscriptions:/)).not.toBeInTheDocument();
  });

  it('shows a generic alert when the action itself throws', async () => {
    mocks.sync.mockRejectedValue(new Error('network'));
    render(<SyncPanel />);

    await setupUser().click(screen.getByRole('button', { name: /check against stripe/i }));

    expect(await screen.findByRole('alert')).toHaveTextContent('could not be completed');
  });

  it('disables the button while checking and drops an old report after a failed rerun', async () => {
    let finish: (value: unknown) => void = () => {};
    mocks.sync.mockResolvedValueOnce({ success: true, data: REPORT });
    mocks.sync.mockReturnValueOnce(new Promise((resolve) => (finish = resolve)));
    render(<SyncPanel />);
    const user = setupUser();
    await user.click(screen.getByRole('button', { name: /check against stripe/i }));
    await screen.findByText(/Read 5 Stripe subscriptions/);
    // The report is shown while the first transition is still closing, when the button still reads
    // "Checking...": wait for it to be the idle button again before the second click (FREEJ-64).
    await user.click(await findIdleControl('button', /check against stripe/i));

    // The pending label is set synchronously by the click, so it is read without waiting.
    expect(screen.getByRole('button', { name: /checking/i })).toBeDisabled();
    finish({ success: false, error: 'Something went wrong.' });
    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(screen.queryByText(/Read 5 Stripe subscriptions/)).not.toBeInTheDocument();
  });

  it('says when Stripe rows could not be read', async () => {
    mocks.sync.mockResolvedValue({ success: true, data: { ...REPORT, skipped_rows: 2 } });
    render(<SyncPanel />);

    await setupUser().click(screen.getByRole('button', { name: /check against stripe/i }));

    expect(await screen.findByText(/2 Stripe row\(s\) could not be read/)).toBeInTheDocument();
  });
});
