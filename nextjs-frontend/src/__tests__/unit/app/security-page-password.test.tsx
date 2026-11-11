import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { setupUser } from '@/__tests__/unit/helpers/user';

/**
 * the password change re-authenticates with the CURRENT password, forwards that fresh ID token and the
 * new password to the server, and then signs in again with the NEW password, because Firebase revokes the old
 * session when a password changes.
 */
const mocks = vi.hoisted(() => ({
  changePasswordAction: vi.fn(),
  reauthenticate: vi.fn(),
  restoreSessionWithNewPassword: vi.fn(),
  push: vi.fn(),
  signOut: vi.fn(),
  toast: vi.fn(),
}));

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: mocks.push, refresh: vi.fn() }) }));
vi.mock('@/lib/firebase/client', () => ({ auth: { currentUser: { email: 'a@b.test' } } }));
vi.mock('firebase/auth', () => ({
  signOut: mocks.signOut,
  onAuthStateChanged: vi.fn(() => vi.fn()),
}));
vi.mock('@/lib/firebase/reauth', () => ({
  reauthenticate: mocks.reauthenticate,
  restoreSessionWithNewPassword: mocks.restoreSessionWithNewPassword,
  reauthMethodFor: () => 'password',
}));
vi.mock('@/app/actions/user-actions', () => ({
  changePasswordAction: mocks.changePasswordAction,
  deleteAccountAction: vi.fn(),
  disableTwoFactorAction: vi.fn(),
  exportMyDataAction: vi.fn(),
  getLoginHistoryAction: vi.fn().mockResolvedValue({ success: true, data: { history: [] } }),
  getTwoFactorStatusAction: vi.fn().mockResolvedValue({ success: true, data: { enabled: false } }),
}));
vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: mocks.toast }) }));

import SecurityPage from '@/app/dashboard/security/page';
import { isSessionRefreshPaused } from '@/lib/firebase/session-refresh-pause';

async function submitPasswordChange() {
  const user = setupUser();
  render(<SecurityPage />);
  await user.click(await screen.findByRole('button', { name: 'Change Password' }));
  await user.type(await screen.findByLabelText('Current Password'), 'old-password');
  await user.type(screen.getByLabelText('New Password'), 'new-password-1');
  await user.type(screen.getByLabelText('Confirm New Password'), 'new-password-1');
  const buttons = screen.getAllByRole('button', { name: 'Change Password' });
  await user.click(buttons[buttons.length - 1]);
}

// Typing through the dialog with userEvent is slow when the whole suite runs in parallel.
vi.setConfig({ testTimeout: 30_000 });

describe('SecurityPage password change', () => {
  it('pauses the background session refresh during the flow and resumes it afterwards', async () => {
    let pausedDuringRestore: boolean | undefined;
    mocks.restoreSessionWithNewPassword.mockImplementation(async () => {
      pausedDuringRestore = isSessionRefreshPaused();
      return true;
    });

    await submitPasswordChange();

    await waitFor(() => expect(mocks.restoreSessionWithNewPassword).toHaveBeenCalled());
    expect(pausedDuringRestore).toBe(true);
    await waitFor(() => expect(isSessionRefreshPaused()).toBe(false));
  });

  beforeEach(() => {
    vi.clearAllMocks();
    mocks.signOut.mockResolvedValue(undefined);
    mocks.reauthenticate.mockResolvedValue({ idToken: 'fresh-id-token', error: null });
    mocks.changePasswordAction.mockResolvedValue({ success: true });
    mocks.restoreSessionWithNewPassword.mockResolvedValue(true);
  });

  it('re-authenticates, changes the password, then signs in again with the new password', async () => {
    await submitPasswordChange();

    await waitFor(() => expect(mocks.restoreSessionWithNewPassword).toHaveBeenCalled());
    expect(mocks.reauthenticate).toHaveBeenCalledWith('password', 'old-password');
    expect(mocks.changePasswordAction).toHaveBeenCalledWith('fresh-id-token', 'new-password-1');
    expect(mocks.restoreSessionWithNewPassword).toHaveBeenCalledWith('new-password-1');
    expect(mocks.push).not.toHaveBeenCalled();
    expect(mocks.toast).toHaveBeenCalledWith(
      expect.objectContaining({ description: 'Password changed successfully' })
    );
  });

  it('stops before the server when the current password is wrong', async () => {
    mocks.reauthenticate.mockResolvedValue({ idToken: null, error: 'Incorrect password' });

    await submitPasswordChange();

    await waitFor(() =>
      expect(mocks.toast).toHaveBeenCalledWith(
        expect.objectContaining({ description: 'Incorrect password', variant: 'destructive' })
      )
    );
    expect(mocks.changePasswordAction).not.toHaveBeenCalled();
    expect(mocks.restoreSessionWithNewPassword).not.toHaveBeenCalled();
  });

  it('does not sign in again when the server refuses the change', async () => {
    mocks.changePasswordAction.mockResolvedValue({
      success: false,
      error: 'Password does not meet requirements',
    });

    await submitPasswordChange();

    await waitFor(() =>
      expect(mocks.toast).toHaveBeenCalledWith(
        expect.objectContaining({ description: 'Password does not meet requirements' })
      )
    );
    expect(mocks.restoreSessionWithNewPassword).not.toHaveBeenCalled();
  });

  it('signs out and sends the user to /login when the new session cannot be restored', async () => {
    mocks.restoreSessionWithNewPassword.mockResolvedValue(false);

    await submitPasswordChange();

    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith('/login'));
    expect(mocks.signOut).toHaveBeenCalled();
    expect(mocks.toast).toHaveBeenCalledWith(
      expect.objectContaining({ description: 'Please sign in again with your new password.' })
    );
  });
});
