import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { setupUser } from '@/__tests__/unit/helpers/user';

/** the redesigned security page (StatusPill history, 2FA code field, accessible errors, danger zone). */
const mocks = vi.hoisted(() => ({
  getTwoFactorStatusAction: vi.fn(),
  getLoginHistoryAction: vi.fn(),
  reauthenticate: vi.fn(),
  changePasswordAction: vi.fn(),
  disableTwoFactorAction: vi.fn(),
  exportMyDataAction: vi.fn(),
}));

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }) }));
vi.mock('@/lib/firebase/client', () => ({ auth: { currentUser: { email: 'a@b.test' } } }));
vi.mock('firebase/auth', () => ({ signOut: vi.fn(), onAuthStateChanged: vi.fn(() => vi.fn()) }));
vi.mock('@/lib/firebase/reauth', () => ({
  reauthenticate: mocks.reauthenticate,
  restoreSessionWithNewPassword: vi.fn(),
  reauthMethodFor: () => 'password',
}));
vi.mock('@/app/actions/user-actions', () => ({
  changePasswordAction: mocks.changePasswordAction,
  deleteAccountAction: vi.fn(),
  disableTwoFactorAction: mocks.disableTwoFactorAction,
  exportMyDataAction: vi.fn(),
  getLoginHistoryAction: mocks.getLoginHistoryAction,
  getTwoFactorStatusAction: mocks.getTwoFactorStatusAction,
}));
vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));

import SecurityPage from '@/app/dashboard/security/page';

vi.setConfig({ testTimeout: 30_000 });

describe('SecurityPage redesign', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getTwoFactorStatusAction.mockResolvedValue({ success: true, data: { enabled: true } });
    mocks.getLoginHistoryAction.mockResolvedValue({
      success: true,
      data: {
        history: [
          { id: '1', loginAt: '2026-10-01T10:00:00Z', ipAddress: '203.0.113.5', successful: true },
          { id: '2', loginAt: '2026-10-02T10:00:00Z', ipAddress: null, successful: false },
        ],
      },
    });
    mocks.reauthenticate.mockResolvedValue({ idToken: 'fresh', error: null });
  });

  it('renders one h1 and a panel heading per section', async () => {
    render(<SecurityPage />);

    expect(await screen.findByRole('heading', { level: 1, name: 'Security' })).toBeInTheDocument();
    for (const name of ['Password', 'Two-Factor Authentication', 'Login History', 'Danger Zone']) {
      expect(screen.getByRole('heading', { level: 2, name })).toBeInTheDocument();
    }
  });

  it('shows login results as status pills with text, not colour alone', async () => {
    render(<SecurityPage />);

    const success = await screen.findByText('Success');
    expect(success.closest('span')).toHaveClass('bg-good');
    expect(screen.getByText('Failed').closest('span')).toHaveClass('bg-crit');
  });

  it('shows the empty state when there is no history', async () => {
    mocks.getLoginHistoryAction.mockResolvedValue({ success: true, data: { history: [] } });

    render(<SecurityPage />);

    expect(await screen.findByText('No login history available')).toBeInTheDocument();
  });

  it('marks the mismatched confirmation invalid and describes it, without calling the server', async () => {
    const user = setupUser();
    render(<SecurityPage />);
    await user.click(await screen.findByRole('button', { name: 'Change Password' }));
    await user.type(await screen.findByLabelText('Current Password'), 'old-password');
    await user.type(screen.getByLabelText('New Password'), 'new-password-1');
    const confirm = screen.getByLabelText('Confirm New Password');
    await user.type(confirm, 'different-1');
    const buttons = screen.getAllByRole('button', { name: 'Change Password' });
    await user.click(buttons[buttons.length - 1]);

    expect(confirm).toHaveAttribute('aria-invalid', 'true');
    const message = screen.getByText('Passwords do not match');
    expect(confirm).toHaveAttribute('aria-describedby', message.id);
    expect(mocks.reauthenticate).not.toHaveBeenCalled();
    expect(mocks.changePasswordAction).not.toHaveBeenCalled();

    await user.type(confirm, 'x');
    expect(confirm).not.toHaveAttribute('aria-invalid');
  });

  it('uses a single 56px one-time-code field for the 2FA disable dialog', async () => {
    const user = setupUser();
    render(<SecurityPage />);
    await user.click(await screen.findByRole('button', { name: 'Disable 2FA' }));

    const code = await screen.findByLabelText('Authenticator code');
    expect(screen.getAllByLabelText('Authenticator code')).toHaveLength(1);
    expect(code).toHaveAttribute('autocomplete', 'one-time-code');
    expect(code).toHaveAttribute('inputmode', 'numeric');
    // No maxlength: it would cut a pasted "123 456" before the digit filter runs.
    expect(code).not.toHaveAttribute('maxlength');
    expect(code).toHaveClass('h-14');
    await user.type(code, '12a3456789');
    expect(code).toHaveValue('123456');
  });

  it('links a dialog error to the password and code fields', async () => {
    mocks.disableTwoFactorAction.mockResolvedValue({
      success: false,
      error: 'Invalid authenticator code',
    });
    const user = setupUser();
    render(<SecurityPage />);
    await user.click(await screen.findByRole('button', { name: 'Disable 2FA' }));
    const dialog = await screen.findByRole('dialog', { name: 'Disable Two-Factor Authentication' });
    await user.type(within(dialog).getByLabelText('Authenticator code'), '123456');
    await user.type(within(dialog).getByLabelText('Confirm your password'), 'pw');
    await user.click(within(dialog).getByRole('button', { name: 'Disable 2FA' }));

    const alert = await within(dialog).findByRole('alert');
    expect(alert).toHaveTextContent('Invalid authenticator code');
    for (const field of [
      within(dialog).getByLabelText('Authenticator code'),
      within(dialog).getByLabelText('Confirm your password'),
    ]) {
      expect(field).toHaveAttribute('aria-invalid', 'true');
      expect(field).toHaveAttribute('aria-describedby', alert.id);
    }
  });

  it('confirms account deletion in a dialog with a destructive button', async () => {
    const user = setupUser();
    render(<SecurityPage />);
    await user.click(await screen.findByRole('button', { name: 'Delete Account' }));

    const dialog = await screen.findByRole('dialog', { name: 'Delete Account' });
    const confirm = within(dialog).getByRole('button', { name: 'Delete Account' });
    expect(confirm).toHaveClass('bg-destructive');
    expect(confirm).toBeDisabled();
  });

  it('clears the typed passwords when the dialog is dismissed with Escape', async () => {
    const user = setupUser();
    render(<SecurityPage />);
    await user.click(await screen.findByRole('button', { name: 'Change Password' }));
    await user.type(await screen.findByLabelText('Current Password'), 'old-password');
    await user.type(screen.getByLabelText('New Password'), 'new-password-1');
    await user.type(screen.getByLabelText('Confirm New Password'), 'different-1');
    await user.click(screen.getAllByRole('button', { name: 'Change Password' }).at(-1)!);
    expect(screen.getByText('Passwords do not match')).toBeInTheDocument();

    await user.keyboard('{Escape}');
    await user.click(await screen.findByRole('button', { name: 'Change Password' }));

    expect(await screen.findByLabelText('Current Password')).toHaveValue('');
    expect(screen.getByLabelText('New Password')).toHaveValue('');
    expect(screen.getByLabelText('Confirm New Password')).toHaveValue('');
    expect(screen.queryByText('Passwords do not match')).not.toBeInTheDocument();
  });

  it('does not mark the code field invalid for a wrong-password error', async () => {
    mocks.reauthenticate.mockResolvedValue({ idToken: null, error: 'Incorrect password' });
    const user = setupUser();
    render(<SecurityPage />);
    await user.click(await screen.findByRole('button', { name: 'Disable 2FA' }));
    const dialog = await screen.findByRole('dialog', { name: 'Disable Two-Factor Authentication' });
    await user.type(within(dialog).getByLabelText('Authenticator code'), '123456');
    await user.type(within(dialog).getByLabelText('Confirm your password'), 'wrong');
    await user.click(within(dialog).getByRole('button', { name: 'Disable 2FA' }));

    const alert = await within(dialog).findByRole('alert');
    expect(alert).toHaveTextContent('Incorrect password');
    expect(within(dialog).getByLabelText('Confirm your password')).toHaveAttribute(
      'aria-invalid',
      'true'
    );
    const code = within(dialog).getByLabelText('Authenticator code');
    expect(code).not.toHaveAttribute('aria-invalid');
    expect(code).not.toHaveAttribute('aria-describedby');
    expect(mocks.disableTwoFactorAction).not.toHaveBeenCalled();
  });
});
