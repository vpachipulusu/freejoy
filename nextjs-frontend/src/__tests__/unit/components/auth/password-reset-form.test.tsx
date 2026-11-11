/** reset form states; the outcome never reveals whether an account exists. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { setupUser } from '@/__tests__/unit/helpers/user';
vi.mock('@/config/site', () => ({ siteConfig: { name: 'Acme Shop' } }));
import { getFirebaseErrorMessage } from '@/lib/utils/firebase-errors';

const mocks = vi.hoisted(() => ({ resetPassword: vi.fn() }));
vi.mock('@/lib/firebase/auth-helpers', () => ({ resetPassword: mocks.resetPassword }));

import { PasswordResetForm } from '@/components/auth/password-reset-form';

const submit = (user: ReturnType<typeof userEvent.setup>) =>
  user.click(screen.getByRole('button', { name: /send reset link|sending/i }));

const failWith = (code: string) =>
  mocks.resetPassword.mockResolvedValue({ error: getFirebaseErrorMessage({ code }) });

describe('PasswordResetForm', () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(cleanup);

  it('validates without the browser and focuses the email field', async () => {
    const user = setupUser();
    render(<PasswordResetForm />);

    await user.type(screen.getByLabelText(/email/i), 'nope');
    await submit(user);

    await waitFor(() => expect(screen.getByLabelText(/email/i)).toHaveFocus());
    expect(screen.getByLabelText(/email/i)).toHaveAttribute('aria-invalid', 'true');
    expect(mocks.resetPassword).not.toHaveBeenCalled();
  });

  it('shows the same neutral confirmation whether or not the account exists', async () => {
    const user = setupUser();
    failWith('auth/user-not-found');
    render(<PasswordResetForm />);

    await user.type(screen.getByLabelText(/email/i), 'a@b.com');
    await submit(user);

    expect(await screen.findByText(/if an account exists/i)).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent(/if an account exists/i);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('shows the neutral confirmation for a disabled account too', async () => {
    const user = setupUser();
    failWith('auth/user-disabled');
    render(<PasswordResetForm />);

    await user.type(screen.getByLabelText(/email/i), 'a@b.com');
    await submit(user);

    expect(await screen.findByText(/if an account exists/i)).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent(/if an account exists/i);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('shows the network message in an alert and focuses it', async () => {
    const user = setupUser();
    failWith('auth/network-request-failed');
    render(<PasswordResetForm />);

    await user.type(screen.getByLabelText(/email/i), 'a@b.com');
    await submit(user);

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/could not reach Acme Shop/i);
    await waitFor(() => expect(alert).toHaveFocus());
  });

  it('shows the rate limit message', async () => {
    const user = setupUser();
    failWith('auth/too-many-requests');
    render(<PasswordResetForm />);

    await user.type(screen.getByLabelText(/email/i), 'a@b.com');
    await submit(user);

    expect(await screen.findByRole('alert')).toHaveTextContent(/wait a few minutes/i);
  });

  it('shows a loading label and sends once', async () => {
    const user = setupUser();
    mocks.resetPassword.mockReturnValue(new Promise(() => {}));
    render(<PasswordResetForm />);

    await user.type(screen.getByLabelText(/email/i), 'a@b.com');
    await submit(user);
    const button = await screen.findByRole('button', { name: /sending reset link/i });
    expect(button).toBeDisabled();
    await user.click(button);

    expect(mocks.resetPassword).toHaveBeenCalledTimes(1);
  });
});
