/** sign-up form states. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { setupUser, type TestUser } from '@/__tests__/unit/helpers/user';
import { recordNavigationAnnouncements } from '@/__tests__/unit/helpers/navigation-announcements';
vi.mock('@/config/site', () => ({ siteConfig: { name: 'Acme Shop' } }));
import { getFirebaseErrorMessage } from '@/lib/utils/firebase-errors';

const mocks = vi.hoisted(() => ({
  signUpWithEmail: vi.fn(),
  verifyTokenAction: vi.fn(),
  push: vi.fn(),
}));
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: mocks.push }) }));
vi.mock('@/lib/firebase/auth-helpers', () => ({ signUpWithEmail: mocks.signUpWithEmail }));
vi.mock('@/app/actions/auth-actions', () => ({ verifyTokenAction: mocks.verifyTokenAction }));

import { SignupForm } from '@/components/auth/signup-form';

async function fill(user: TestUser, name = 'Test User') {
  if (name) await user.type(screen.getByLabelText(/full name/i), name);
  await user.type(screen.getByLabelText(/email/i), 'a@b.com');
  await user.type(screen.getByLabelText(/^password$/i), 'ValidPass1');
  await user.type(screen.getByLabelText(/confirm password/i), 'ValidPass1');
  await user.click(screen.getByRole('button', { name: /create account/i }));
}

describe('SignupForm', () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(cleanup);

  it('focuses the first invalid field on submit', async () => {
    const user = setupUser();
    render(<SignupForm />);

    await fill(user, '');

    await waitFor(() => expect(screen.getByLabelText(/full name/i)).toHaveFocus());
    expect(screen.getByLabelText(/full name/i)).toHaveAttribute('aria-invalid', 'true');
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('does not reveal that the email is already registered', async () => {
    const user = setupUser();
    mocks.signUpWithEmail.mockResolvedValue({
      user: null,
      idToken: null,
      error: getFirebaseErrorMessage({ code: 'auth/email-already-in-use' }),
    });
    render(<SignupForm />);

    await fill(user);

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('We could not create your account');
    expect(alert).not.toHaveTextContent(/already registered|in use/i);
    await waitFor(() => expect(alert).toHaveFocus());
  });

  it('shows the network message', async () => {
    const user = setupUser();
    mocks.signUpWithEmail.mockRejectedValue(new TypeError('Failed to fetch'));
    vi.spyOn(console, 'error').mockImplementation(() => {});
    render(<SignupForm />);

    await fill(user);

    expect(await screen.findByRole('alert')).toHaveTextContent(/could not reach Acme Shop/i);
  });

  it('shows a loading label and submits once', async () => {
    const user = setupUser();
    mocks.signUpWithEmail.mockReturnValue(new Promise(() => {}));
    render(<SignupForm />);

    await fill(user);
    const button = await screen.findByRole('button', { name: /creating account/i });
    expect(button).toBeDisabled();
    await user.click(button);

    expect(mocks.signUpWithEmail).toHaveBeenCalledTimes(1);
  });

  it('names an outage when the action sets the typed flag', async () => {
    const user = setupUser();
    mocks.signUpWithEmail.mockResolvedValue({ user: {}, idToken: 't', error: null });
    mocks.verifyTokenAction.mockResolvedValue({
      success: false,
      error: 'Sign-in service unavailable',
      data: { serviceUnavailable: true },
    });
    const announced = recordNavigationAnnouncements();
    render(<SignupForm />);

    await fill(user);

    expect(await screen.findByRole('alert')).toHaveTextContent(/sign-in is unavailable right now/i);
    expect(announced.hrefs).toEqual([]);
  });

  it('goes to verify-email on success', async () => {
    const announced = recordNavigationAnnouncements();
    const user = setupUser();
    mocks.signUpWithEmail.mockResolvedValue({ user: {}, idToken: 't', error: null });
    mocks.verifyTokenAction.mockResolvedValue({ success: true });
    render(<SignupForm />);

    await fill(user);

    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith('/verify-email'));
    expect(announced.hrefs).toEqual(['/verify-email']);
  });
});
