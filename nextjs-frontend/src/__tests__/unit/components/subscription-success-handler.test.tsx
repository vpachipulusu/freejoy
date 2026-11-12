import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';

/**
 * when the plan has not flipped after the poll window, the dashboard keeps telling the user their payment
 * went through (it used to strip ?upgrade=success after a second, so the banner vanished and nothing was explained).
 */

const mocks = vi.hoisted(() => {
  const replace = vi.fn();
  const refresh = vi.fn();
  return {
    replace,
    refresh,
    router: { replace, refresh },
    searchParams: new URLSearchParams('upgrade=success'),
    getSubscriptionStatusAction: vi.fn(),
    verifyUpgradeSessionAction: vi.fn(),
  };
});

vi.mock('next/navigation', () => ({
  useRouter: () => mocks.router,
  useSearchParams: () => mocks.searchParams,
}));
vi.mock('@/app/actions/subscription-actions', () => ({
  getSubscriptionStatusAction: mocks.getSubscriptionStatusAction,
  verifyUpgradeSessionAction: mocks.verifyUpgradeSessionAction,
}));

import { SubscriptionSuccessHandler } from '@/components/dashboard/subscription-success-handler';
import { recordNavigationAnnouncements } from '@/__tests__/unit/helpers/navigation-announcements';

const FREE = { success: true, data: { subscriptionTier: 'free', subscriptionStatus: 'inactive' } };
const PRO = { success: true, data: { subscriptionTier: 'pro', subscriptionStatus: 'active' } };

async function renderAndAdvance(ms: number) {
  const view = render(<SubscriptionSuccessHandler />);
  // Let verification and the first status check settle before moving the clock, or timers registered by them
  // would be scheduled after the whole advance has already elapsed.
  for (let i = 0; i < 5; i += 1) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
  }
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
  return view;
}

describe('SubscriptionSuccessHandler', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] });
    vi.clearAllMocks();
    mocks.verifyUpgradeSessionAction.mockResolvedValue({ success: true, data: { valid: true } });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('tells the user the payment was received and keeps the banner when the plan never flips', async () => {
    const announced = recordNavigationAnnouncements();
    mocks.getSubscriptionStatusAction.mockResolvedValue(FREE);

    await renderAndAdvance(40_000);

    expect(screen.getByText(/payment received/i)).toBeInTheDocument();
    expect(screen.getByText(/contact support/i)).toBeInTheDocument();
    expect(mocks.replace).not.toHaveBeenCalledWith('/dashboard');
    expect(announced.hrefs).toEqual([]);
  });

  it('shows the processing state while it is still polling', async () => {
    mocks.getSubscriptionStatusAction.mockResolvedValue(FREE);

    await renderAndAdvance(3_000);

    expect(screen.getByText(/processing your subscription/i)).toBeInTheDocument();
  });

  it('activates and refreshes the dashboard once the plan is Pro', async () => {
    const announced = recordNavigationAnnouncements();
    mocks.getSubscriptionStatusAction.mockResolvedValue(PRO);

    await renderAndAdvance(5_000);

    expect(screen.getByText(/subscription activated/i)).toBeInTheDocument();
    expect(mocks.replace).toHaveBeenCalledWith('/dashboard');
    expect(announced.hrefs).toEqual(['/dashboard']);
  });

  it('removes a forged ?upgrade=success straight away', async () => {
    const announced = recordNavigationAnnouncements();
    mocks.verifyUpgradeSessionAction.mockResolvedValue({
      success: true,
      data: { valid: false, reason: 'No valid checkout session found' },
    });

    await renderAndAdvance(100);

    expect(mocks.replace).toHaveBeenCalledWith('/dashboard');
    expect(announced.hrefs).toEqual(['/dashboard']);
    expect(mocks.getSubscriptionStatusAction).not.toHaveBeenCalled();
  });

  it('keeps the processing state until the tenth poll, then times out at about 30 seconds', async () => {
    mocks.getSubscriptionStatusAction.mockResolvedValue(FREE);
    render(<SubscriptionSuccessHandler />);
    for (let i = 0; i < 5; i += 1) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
    }

    await act(async () => {
      await vi.advanceTimersByTimeAsync(27_000);
    });
    expect(screen.getByText(/processing your subscription/i)).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_000);
    });
    expect(screen.getByText(/payment received/i)).toBeInTheDocument();
  });

  it('stops polling and refreshes once after the timeout', async () => {
    mocks.getSubscriptionStatusAction.mockResolvedValue(FREE);

    await renderAndAdvance(60_000);

    // The first check, ten polls and one final check before giving up, then nothing more.
    expect(mocks.getSubscriptionStatusAction).toHaveBeenCalledTimes(12);
    expect(mocks.refresh).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('lets the user dismiss the timed-out banner', async () => {
    mocks.getSubscriptionStatusAction.mockResolvedValue(FREE);

    await renderAndAdvance(40_000);
    fireEvent.click(screen.getByRole('button', { name: /dismiss/i }));

    expect(mocks.replace).toHaveBeenCalledWith('/dashboard');
  });

  it('clears its timers when it unmounts while polling', async () => {
    mocks.getSubscriptionStatusAction.mockResolvedValue(FREE);

    const { unmount } = await renderAndAdvance(7_000);
    unmount();

    expect(vi.getTimerCount()).toBe(0);
  });

  it('refreshes once when the plan turns active, not on a timeout', async () => {
    mocks.getSubscriptionStatusAction.mockResolvedValue(PRO);

    await renderAndAdvance(5_000);

    expect(mocks.refresh).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(/payment received/i)).not.toBeInTheDocument();
  });

  it('does one last check before giving up, so a late webhook never leaves the timeout banner beside Pro', async () => {
    // Free for the first check and all ten polls, active on the final check.
    mocks.getSubscriptionStatusAction.mockResolvedValue(FREE);
    mocks.getSubscriptionStatusAction.mockResolvedValueOnce(FREE);
    for (let i = 0; i < 10; i += 1) mocks.getSubscriptionStatusAction.mockResolvedValueOnce(FREE);
    mocks.getSubscriptionStatusAction.mockResolvedValueOnce(PRO);

    await renderAndAdvance(40_000);
    // The activation redirect timer is scheduled once React has rendered the success state.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_000);
    });

    expect(screen.getByText(/subscription activated/i)).toBeInTheDocument();
    expect(screen.queryByText(/payment received/i)).not.toBeInTheDocument();
    expect(mocks.replace).toHaveBeenCalledWith('/dashboard');
  });
});
