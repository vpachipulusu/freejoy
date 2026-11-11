/** sign-in form states: alert panel, focus, loading and double submit. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { setupUser } from '@/__tests__/unit/helpers/user';
vi.mock('@/config/site', () => ({ siteConfig: { name: 'Acme Shop' } }));
import { getFirebaseErrorMessage } from '@/lib/utils/firebase-errors';

const mocks = vi.hoisted(() => ({
  signInWithEmail: vi.fn(),
  verifyTokenAction: vi.fn(),
  push: vi.fn(),
  refresh: vi.fn(),
}));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: mocks.push, refresh: mocks.refresh }),
}));
vi.mock('@/lib/firebase/auth-helpers', () => ({ signInWithEmail: mocks.signInWithEmail }));
vi.mock('@/app/actions/auth-actions', () => ({ verifyTokenAction: mocks.verifyTokenAction }));

import { LoginForm } from '@/components/auth/login-form';
import { REDIRECT_RECOVERY_MS } from '@/components/auth/auth-form-errors';

const onTotpRequired = vi.fn();

const fail = (code: string) => ({
  user: null,
  idToken: null,
  error: getFirebaseErrorMessage({ code }),
});

async function fillAndSubmit(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText(/email/i), 'a@b.com');
  await user.type(screen.getByLabelText(/^password$/i), 'secret123');
  await user.click(screen.getByRole('button', { name: /sign in/i }));
}

describe('LoginForm', () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(cleanup);

  it('moves focus to the first invalid field on submit', async () => {
    const user = setupUser();
    render(<LoginForm onTotpRequired={onTotpRequired} />);

    await user.type(screen.getByLabelText(/^password$/i), 'secret123');
    await user.click(screen.getByRole('button', { name: /sign in/i }));

    const email = screen.getByLabelText(/email/i);
    await waitFor(() => expect(email).toHaveFocus());
    expect(email).toHaveAttribute('aria-invalid', 'true');
    expect(email).toHaveAccessibleDescription(/valid email/i);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it.each(['auth/user-not-found', 'auth/wrong-password'])(
    'shows the same neutral alert for %s and focuses it',
    async (code) => {
      const user = setupUser();
      mocks.signInWithEmail.mockResolvedValue(fail(code));
      render(<LoginForm onTotpRequired={onTotpRequired} />);

      await fillAndSubmit(user);

      const alert = await screen.findByRole('alert');
      expect(alert).toHaveTextContent('We could not sign you in');
      expect(alert).toHaveTextContent(/email and password do not match/i);
      expect(alert).not.toHaveTextContent(/no account|not found|incorrect password/i);
      await waitFor(() => expect(alert).toHaveFocus());
    }
  );

  it('shows the network message', async () => {
    const user = setupUser();
    mocks.signInWithEmail.mockResolvedValue(fail('auth/network-request-failed'));
    render(<LoginForm onTotpRequired={onTotpRequired} />);

    await fillAndSubmit(user);

    expect(await screen.findByRole('alert')).toHaveTextContent(
      /could not reach Acme Shop.*check your connection/i
    );
  });

  it('shows the rate limit message with the wait', async () => {
    const user = setupUser();
    mocks.signInWithEmail.mockResolvedValue(fail('auth/too-many-requests'));
    render(<LoginForm onTotpRequired={onTotpRequired} />);

    await fillAndSubmit(user);

    expect(await screen.findByRole('alert')).toHaveTextContent(/wait a few minutes/i);
  });

  it('shows the suspended message with a support link', async () => {
    const user = setupUser();
    mocks.signInWithEmail.mockResolvedValue(fail('auth/user-disabled'));
    render(<LoginForm onTotpRequired={onTotpRequired} />);

    await fillAndSubmit(user);

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('This account is suspended');
    expect(screen.getByRole('link', { name: /contact support/i })).toHaveAttribute(
      'href',
      '/contact'
    );
  });

  it('hides the raw server refusal text', async () => {
    const user = setupUser();
    mocks.signInWithEmail.mockResolvedValue({ user: {}, idToken: 't', error: null });
    mocks.verifyTokenAction.mockResolvedValue({
      success: false,
      error: 'Token verification failed',
    });
    render(<LoginForm onTotpRequired={onTotpRequired} />);

    await fillAndSubmit(user);

    const alert = await screen.findByRole('alert');
    expect(alert).not.toHaveTextContent(/token/i);
    expect(alert).toHaveTextContent(/try again/i);
  });

  it('names an outage when the action sets the typed flag', async () => {
    const user = setupUser();
    mocks.signInWithEmail.mockResolvedValue({ user: {}, idToken: 't', error: null });
    mocks.verifyTokenAction.mockResolvedValue({
      success: false,
      error: 'Sign-in service unavailable',
      data: { serviceUnavailable: true },
    });
    render(<LoginForm onTotpRequired={onTotpRequired} />);

    await fillAndSubmit(user);

    expect(await screen.findByRole('alert')).toHaveTextContent(/sign-in is unavailable right now/i);
  });

  it('does not name an outage from the message text alone', async () => {
    const user = setupUser();
    mocks.signInWithEmail.mockResolvedValue({ user: {}, idToken: 't', error: null });
    mocks.verifyTokenAction.mockResolvedValue({
      success: false,
      error: 'Sign-in service unavailable',
    });
    render(<LoginForm onTotpRequired={onTotpRequired} />);

    await fillAndSubmit(user);

    expect(await screen.findByRole('alert')).not.toHaveTextContent(/unavailable right now/i);
  });

  it('treats a thrown error as a network problem', async () => {
    const user = setupUser();
    mocks.signInWithEmail.mockRejectedValue(new TypeError('Failed to fetch'));
    vi.spyOn(console, 'error').mockImplementation(() => {});
    render(<LoginForm onTotpRequired={onTotpRequired} />);

    await fillAndSubmit(user);

    expect(await screen.findByRole('alert')).toHaveTextContent(/could not reach Acme Shop/i);
  });

  it('clears the alert on the next submit', async () => {
    const user = setupUser();
    mocks.signInWithEmail.mockResolvedValueOnce(fail('auth/wrong-password'));
    mocks.signInWithEmail.mockReturnValueOnce(new Promise(() => {}));
    render(<LoginForm onTotpRequired={onTotpRequired} />);
    await fillAndSubmit(user);
    await screen.findByRole('alert');

    await user.click(screen.getByRole('button', { name: /sign in/i }));

    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
  });

  it('shows a loading label and submits once on a double submit', async () => {
    const user = setupUser();
    mocks.signInWithEmail.mockReturnValue(new Promise(() => {}));
    render(<LoginForm onTotpRequired={onTotpRequired} />);

    await fillAndSubmit(user);
    const button = await screen.findByRole('button', { name: /signing in/i });
    expect(button).toBeDisabled();
    await user.click(button);
    await user.keyboard('{Enter}');

    expect(mocks.signInWithEmail).toHaveBeenCalledTimes(1);
    expect(button.closest('form')).toHaveAttribute('aria-busy', 'true');
  });

  it('does not claim a network problem for other thrown errors', async () => {
    const user = setupUser();
    mocks.signInWithEmail.mockRejectedValue(new Error('boom'));
    vi.spyOn(console, 'error').mockImplementation(() => {});
    render(<LoginForm onTotpRequired={onTotpRequired} />);

    await fillAndSubmit(user);

    const alert = await screen.findByRole('alert');
    expect(alert).not.toHaveTextContent(/reach Acme Shop|connection/i);
    expect(alert).toHaveTextContent(/try again/i);
  });

  it('gives the form back when the redirect throws', async () => {
    const user = setupUser();
    mocks.signInWithEmail.mockResolvedValue({ user: {}, idToken: 't', error: null });
    mocks.verifyTokenAction.mockResolvedValue({ success: true });
    mocks.push.mockImplementationOnce(() => {
      throw new Error('navigation failed');
    });
    vi.spyOn(console, 'error').mockImplementation(() => {});
    render(<LoginForm onTotpRequired={onTotpRequired} />);

    await fillAndSubmit(user);

    await screen.findByRole('alert');
    expect(screen.getByRole('button', { name: /sign in/i })).toBeEnabled();
  });

  it('gives the form back when the redirect stalls', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const user = setupUser({ advanceTimers: vi.advanceTimersByTime });
      mocks.signInWithEmail.mockResolvedValue({ user: {}, idToken: 't', error: null });
      mocks.verifyTokenAction.mockResolvedValue({ success: true });
      render(<LoginForm onTotpRequired={onTotpRequired} />);

      await fillAndSubmit(user);
      await waitFor(() => expect(mocks.push).toHaveBeenCalled());
      expect(screen.getByRole('button', { name: /signing in/i })).toBeDisabled();

      await act(async () => {
        vi.advanceTimersByTime(REDIRECT_RECOVERY_MS + 100);
      });

      expect(screen.getByRole('button', { name: /sign in/i })).toBeEnabled();
    } finally {
      vi.useRealTimers();
    }
  });

  it('redirects to the dashboard on success', async () => {
    const user = setupUser();
    mocks.signInWithEmail.mockResolvedValue({ user: {}, idToken: 't', error: null });
    mocks.verifyTokenAction.mockResolvedValue({ success: true });
    render(<LoginForm onTotpRequired={onTotpRequired} />);

    await fillAndSubmit(user);

    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith('/dashboard'));
  });

  it('hands the ID token to the two-factor step when the account needs a code', async () => {
    const user = setupUser();
    mocks.signInWithEmail.mockResolvedValue({ user: {}, idToken: 'tok-1', error: null });
    mocks.verifyTokenAction.mockResolvedValue({
      success: false,
      error: 'Two-factor code required',
      data: { totpRequired: true },
    });
    render(<LoginForm onTotpRequired={onTotpRequired} />);

    await fillAndSubmit(user);

    await waitFor(() => expect(onTotpRequired).toHaveBeenCalledWith('tok-1'));
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(mocks.push).not.toHaveBeenCalled();
    // The form is usable again for when the person comes back from the code step.
    expect(screen.getByRole('button', { name: /^sign in$/i })).toBeEnabled();
  });

  it('does not open the two-factor step from the message text alone', async () => {
    const user = setupUser();
    mocks.signInWithEmail.mockResolvedValue({ user: {}, idToken: 'tok-1', error: null });
    mocks.verifyTokenAction.mockResolvedValue({
      success: false,
      error: 'Two-factor code required',
    });
    render(<LoginForm onTotpRequired={onTotpRequired} />);

    await fillAndSubmit(user);

    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(onTotpRequired).not.toHaveBeenCalled();
  });

  it('shows the rate limit message when the API limits the sign-in', async () => {
    const user = setupUser();
    mocks.signInWithEmail.mockResolvedValue({ user: {}, idToken: 'tok-1', error: null });
    mocks.verifyTokenAction.mockResolvedValue({
      success: false,
      error: 'Too many sign-in attempts',
      data: { rateLimited: true },
    });
    render(<LoginForm onTotpRequired={onTotpRequired} />);

    await fillAndSubmit(user);

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Too many attempts');
    expect(alert).not.toHaveTextContent('Too many sign-in attempts');
  });
});
