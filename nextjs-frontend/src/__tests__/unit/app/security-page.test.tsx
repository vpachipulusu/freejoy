import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const mocks = vi.hoisted(() => ({
  getTwoFactorStatusAction: vi.fn(),
  getLoginHistoryAction: vi.fn(),
  exportMyDataAction: vi.fn(),
  toast: vi.fn(),
}));

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }) }));
vi.mock('@/lib/firebase/client', () => ({
  auth: { currentUser: { email: 'a@b.test', providerData: [{ providerId: 'password' }] } },
}));
vi.mock('firebase/auth', () => ({ signOut: vi.fn(), onAuthStateChanged: vi.fn(() => vi.fn()) }));
vi.mock('@/app/actions/user-actions', () => ({
  changePasswordAction: vi.fn(),
  deleteAccountAction: vi.fn(),
  disableTwoFactorAction: vi.fn(),
  exportMyDataAction: mocks.exportMyDataAction,
  getLoginHistoryAction: mocks.getLoginHistoryAction,
  getTwoFactorStatusAction: mocks.getTwoFactorStatusAction,
}));
vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: mocks.toast }) }));

import SecurityPage from '@/app/dashboard/security/page';

describe('SecurityPage two-factor status', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getLoginHistoryAction.mockResolvedValue({ success: true, data: { history: [] } });
  });

  it.each([
    [true, 'Two-factor authentication is enabled'],
    [false, 'Two-factor authentication is not enabled'],
  ])('shows the API status (enabled=%s)', async (enabled, text) => {
    mocks.getTwoFactorStatusAction.mockResolvedValue({ success: true, data: { enabled } });

    render(<SecurityPage />);

    expect(await screen.findByTestId('two-factor-status')).toHaveTextContent(text);
  });

  it('claims nothing when the status cannot be read', async () => {
    mocks.getTwoFactorStatusAction.mockResolvedValue({
      success: false,
      error: 'Failed to get two-factor status',
    });

    render(<SecurityPage />);

    expect(await screen.findByText('Two-factor status unavailable')).toBeInTheDocument();
    expect(screen.queryByTestId('two-factor-status')).not.toBeInTheDocument();
  });

  it('offers Disable 2FA only when two-factor is enabled, and never an enrolment button', async () => {
    mocks.getTwoFactorStatusAction.mockResolvedValue({ success: true, data: { enabled: true } });

    render(<SecurityPage />);

    expect(await screen.findByRole('button', { name: 'Disable 2FA' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /enable 2fa/i })).not.toBeInTheDocument();
  });

  it('shows no 2FA buttons when two-factor is off, but always offers account deletion', async () => {
    mocks.getTwoFactorStatusAction.mockResolvedValue({ success: true, data: { enabled: false } });

    render(<SecurityPage />);

    await screen.findByTestId('two-factor-status');
    expect(screen.queryByRole('button', { name: /2fa/i })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Delete Account' })).toBeInTheDocument();
  });
});

describe('SecurityPage data export', () => {
  const originalCreate = URL.createObjectURL;
  const originalRevoke = URL.revokeObjectURL;
  const createUrl = vi.fn((_blob: Blob) => 'blob:export');
  const revokeUrl = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getLoginHistoryAction.mockResolvedValue({ success: true, data: { history: [] } });
    mocks.getTwoFactorStatusAction.mockResolvedValue({ success: true, data: { enabled: false } });
    Object.assign(URL, { createObjectURL: createUrl, revokeObjectURL: revokeUrl });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    Object.assign(URL, { createObjectURL: originalCreate, revokeObjectURL: originalRevoke });
  });

  async function exportWith(result: unknown, downloads: string[] = []) {
    mocks.exportMyDataAction.mockResolvedValue(result);
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
      this: HTMLAnchorElement
    ) {
      downloads.push(this.download);
    });
    render(<SecurityPage />);
    const button = await screen.findByRole('button', { name: 'Export my data' });
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fireEvent.click(button);
    await vi.advanceTimersByTimeAsync(0);
    return click;
  }

  it('downloads the exported JSON as a named file and revokes the URL later', async () => {
    const downloads: string[] = [];
    const click = await exportWith(
      {
        success: true,
        data: { filename: 'acme-shop-export-20261017.json', json: '{"a":1}', truncated: false },
      },
      downloads
    );

    expect(click).toHaveBeenCalledTimes(1);
    expect(downloads).toEqual(['acme-shop-export-20261017.json']);
    expect(createUrl.mock.calls[0][0].type).toBe('application/json');
    expect(revokeUrl).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(revokeUrl).toHaveBeenCalledWith('blob:export');
    expect(mocks.toast).toHaveBeenCalledWith(
      expect.objectContaining({ description: 'Your data download has started.' })
    );
  });

  it('tells the user when older rows were left out of the export', async () => {
    await exportWith({
      success: true,
      data: { filename: 'x.json', json: '{}', truncated: true },
    });

    expect(mocks.toast).toHaveBeenCalledWith(
      expect.objectContaining({ description: expect.stringContaining('Older rows were left out') })
    );
  });

  it('marks the button busy and disabled while the export runs', async () => {
    let finish: (value: unknown) => void = () => {};
    mocks.exportMyDataAction.mockReturnValue(new Promise((resolve) => (finish = resolve)));
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    render(<SecurityPage />);
    const button = await screen.findByRole('button', { name: 'Export my data' });

    fireEvent.click(button);

    await waitFor(() => expect(button).toBeDisabled());
    expect(button).toHaveAttribute('aria-busy', 'true');
    finish({ success: false, error: 'x' });
    await waitFor(() => expect(button).toBeEnabled());
    expect(button).toHaveAttribute('aria-busy', 'false');
  });

  it('shows the error and downloads nothing when the export fails', async () => {
    const click = await exportWith({
      success: false,
      error: 'Too many export requests. Try again later.',
    });

    expect(mocks.toast).toHaveBeenCalledWith(
      expect.objectContaining({ description: 'Too many export requests. Try again later.' })
    );
    expect(click).not.toHaveBeenCalled();
    expect(createUrl).not.toHaveBeenCalled();
  });

  it('shows a toast when the export call itself rejects', async () => {
    mocks.exportMyDataAction.mockRejectedValue(new Error('network'));

    render(<SecurityPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Export my data' }));

    await waitFor(() =>
      expect(mocks.toast).toHaveBeenCalledWith(
        expect.objectContaining({ title: 'Export failed', variant: 'destructive' })
      )
    );
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Export my data' })).toBeEnabled()
    );
  });
});
