/** two-factor sign-in: the code step of the login panel, from the first step to the dashboard. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { StrictMode } from 'react';
import userEvent from '@testing-library/user-event';
import { setupUser } from '@/__tests__/unit/helpers/user';

const mocks = vi.hoisted(() => ({
  signInWithEmail: vi.fn(),
  signInWithGoogle: vi.fn(),
  signInWithGitHub: vi.fn(),
  signOut: vi.fn(),
  verifyTokenAction: vi.fn(),
  toast: vi.fn(),
  push: vi.fn(),
  refresh: vi.fn(),
}));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: mocks.push, refresh: mocks.refresh }),
}));
vi.mock('@/config/site', () => ({ siteConfig: { name: 'Acme Shop' } }));
vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: mocks.toast }) }));
vi.mock('@/lib/firebase/auth-helpers', () => ({
  signInWithEmail: mocks.signInWithEmail,
  signInWithGoogle: mocks.signInWithGoogle,
  signInWithGitHub: mocks.signInWithGitHub,
  signOut: mocks.signOut,
}));
vi.mock('@/app/actions/auth-actions', () => ({ verifyTokenAction: mocks.verifyTokenAction }));

import { LoginPanel } from '@/components/auth/login-panel';
import { TotpChallengeForm } from '@/components/auth/totp-challenge-form';
import { REDIRECT_RECOVERY_MS } from '@/components/auth/auth-form-errors';
import {
  SIGN_IN_RATE_LIMITED_ERROR,
  SIGN_IN_SERVICE_UNAVAILABLE_ERROR,
  TOTP_INVALID_ERROR,
  TOTP_REQUIRED_ERROR,
} from '@/lib/auth/auth-error-messages';

const ID_TOKEN = 'id-token-1';
const CODE = '123456';

const totpRequired = {
  success: false,
  error: TOTP_REQUIRED_ERROR,
  data: { totpRequired: true },
};
const totpInvalid = { success: false, error: TOTP_INVALID_ERROR, data: { totpInvalid: true } };

type User = ReturnType<typeof userEvent.setup>;

async function signInWithPassword(user: User) {
  await user.type(screen.getByLabelText(/email/i), 'a@b.com');
  await user.type(screen.getByLabelText(/^password$/i), 'secret123');
  await user.click(screen.getByRole('button', { name: /^sign in$/i }));
}

/** Gets the panel to the code step. */
async function openCodeStep(user: User) {
  mocks.signInWithEmail.mockResolvedValue({ user: {}, idToken: ID_TOKEN, error: null });
  mocks.verifyTokenAction.mockResolvedValueOnce(totpRequired);
  render(<LoginPanel />);
  await signInWithPassword(user);
  return screen.findByLabelText('Authenticator code');
}

async function submitCode(user: User, code: string) {
  await user.type(screen.getByLabelText('Authenticator code'), code);
  await user.click(screen.getByRole('button', { name: /verify and sign in/i }));
}

describe('two-factor code step', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.signOut.mockResolvedValue({ error: null });
  });
  afterEach(async () => {
    cleanup();
    // The step's deferred abandon check runs on a zero-delay timer: let it settle before the next test.
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

  it('opens when the password step answers totpRequired, forwarding the same ID token later', async () => {
    const user = setupUser();

    const field = await openCodeStep(user);

    expect(mocks.verifyTokenAction).toHaveBeenCalledTimes(1);
    expect(mocks.verifyTokenAction).toHaveBeenCalledWith(ID_TOKEN);
    expect(screen.getByRole('heading', { name: /two-factor authentication/i })).toBeInTheDocument();
    expect(field).toHaveFocus();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(mocks.push).not.toHaveBeenCalled();
  });

  it('is an accessible numeric one-time-code field with a visible label and help text', async () => {
    const user = setupUser();

    const field = await openCodeStep(user);

    expect(field).toHaveAttribute('inputmode', 'numeric');
    expect(field).toHaveAttribute('autocomplete', 'one-time-code');
    // No maxlength: it would cut a pasted "123 456" before the digit filter runs.
    expect(field).not.toHaveAttribute('maxlength');
    expect(field).toHaveAttribute('name', 'totpCode');
    expect(field).not.toHaveAttribute('aria-invalid');
    expect(field).toHaveAccessibleDescription(/six-digit code from your authenticator app/i);
    expect(field.closest('form')).toHaveAttribute('aria-busy', 'false');
  });

  it('keeps only digits', async () => {
    const user = setupUser();
    const field = await openCodeStep(user);

    await user.type(field, '12a 3-4');

    expect(field).toHaveValue('1234');
  });

  it('signs in with the code and goes to the dashboard', async () => {
    const user = setupUser();
    await openCodeStep(user);
    mocks.verifyTokenAction.mockResolvedValueOnce({ success: true, data: {} });

    await submitCode(user, CODE);

    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith('/dashboard'));
    expect(mocks.refresh).toHaveBeenCalled();
    expect(mocks.verifyTokenAction).toHaveBeenLastCalledWith(ID_TOKEN, CODE);
  });

  it('announces a wrong code, clears the field and lets the person retry', async () => {
    const user = setupUser();
    await openCodeStep(user);
    mocks.verifyTokenAction.mockResolvedValueOnce(totpInvalid);

    await submitCode(user, '000000');

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('That code did not work');
    expect(alert).not.toHaveTextContent(TOTP_INVALID_ERROR);
    // Still announced (role=alert, linked by aria-describedby), but the cursor stays in the field so a phone
    // keypad does not close.
    const field = screen.getByLabelText('Authenticator code');
    await waitFor(() => expect(field).toHaveFocus());
    expect(alert).not.toHaveFocus();
    expect(field).toHaveValue('');
    expect(field).toHaveAttribute('aria-invalid', 'true');
    expect(field).toHaveAccessibleDescription(/did not work/i);

    mocks.verifyTokenAction.mockResolvedValueOnce({ success: true, data: {} });
    await submitCode(user, CODE);
    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith('/dashboard'));
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('asks for six digits without calling the server for a short code', async () => {
    const user = setupUser();
    await openCodeStep(user);

    await submitCode(user, '123');

    expect(await screen.findByRole('alert')).toHaveTextContent('Enter the six-digit code');
    expect(mocks.verifyTokenAction).toHaveBeenCalledTimes(1);
  });

  it('shows the rate limit message when too many codes were tried', async () => {
    const user = setupUser();
    await openCodeStep(user);
    mocks.verifyTokenAction.mockResolvedValueOnce({
      success: false,
      error: SIGN_IN_RATE_LIMITED_ERROR,
      data: { rateLimited: true },
    });

    await submitCode(user, CODE);

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Too many attempts');
    expect(alert).toHaveTextContent(/wait a few minutes/i);
    expect(alert).not.toHaveTextContent(SIGN_IN_RATE_LIMITED_ERROR);
  });

  it('names an outage rather than a wrong code', async () => {
    const user = setupUser();
    await openCodeStep(user);
    mocks.verifyTokenAction.mockResolvedValueOnce({
      success: false,
      error: SIGN_IN_SERVICE_UNAVAILABLE_ERROR,
      data: { serviceUnavailable: true },
    });

    await submitCode(user, CODE);

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/sign-in is unavailable right now/i);
    expect(alert).not.toHaveTextContent(/code did not work/i);
  });

  it('closes the code step, signed out, with a notice when the sign-in is too old to finish', async () => {
    const user = setupUser();
    await openCodeStep(user);
    mocks.verifyTokenAction.mockResolvedValueOnce({
      success: false,
      error: 'Token verification failed',
    });

    await submitCode(user, CODE);

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/your sign-in timed out/i);
    expect(alert).toHaveTextContent(/sign in again/i);
    expect(alert).not.toHaveTextContent(/token/i);
    expect(screen.queryByLabelText('Authenticator code')).not.toBeInTheDocument();
    expect(screen.getByLabelText(/email/i)).toBeVisible();
    expect(mocks.signOut).toHaveBeenCalledTimes(1);
    // The password step plus the one code call: nothing retries after the step is closed.
    expect(mocks.verifyTokenAction).toHaveBeenCalledTimes(2);
  });

  it('shows the timed-out notice until the next sign-in attempt', async () => {
    const user = setupUser();
    await openCodeStep(user);
    mocks.verifyTokenAction.mockResolvedValueOnce({
      success: false,
      error: 'Token verification failed',
    });
    await submitCode(user, CODE);
    await screen.findByRole('alert');
    mocks.signInWithEmail.mockReturnValue(new Promise(() => {}));

    await user.type(screen.getByLabelText(/^password$/i), 'secret123');
    await user.click(screen.getByRole('button', { name: /^sign in$/i }));

    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
  });

  it('keeps only digits from a paste and takes the first six', async () => {
    const user = setupUser();
    const field = await openCodeStep(user);

    await user.click(field);
    await user.paste('123 456');
    expect(field).toHaveValue('123456');

    await user.clear(field);
    await user.paste('123-456');
    expect(field).toHaveValue('123456');

    await user.clear(field);
    await user.paste('1234567');
    expect(field).toHaveValue('123456');
  });

  it('announces a second short code again (a new alert, not the same one)', async () => {
    const user = setupUser();
    await openCodeStep(user);

    await submitCode(user, '123');
    const first = await screen.findByRole('alert');
    await user.click(screen.getByRole('button', { name: /verify and sign in/i }));

    const second = screen.getByRole('alert');
    expect(second).toHaveTextContent('Enter the six-digit code');
    expect(second).not.toBe(first);
    expect(first).not.toBeInTheDocument();
  });

  it('empties the password field when the code step opens', async () => {
    const user = setupUser();
    await openCodeStep(user);

    expect(screen.getByLabelText(/^password$/i)).toHaveValue('');
    expect(screen.getByLabelText(/email/i)).toHaveValue('a@b.com');
  });

  it('treats a thrown network error as a connection problem, without echoing it', async () => {
    const user = setupUser();
    await openCodeStep(user);
    mocks.verifyTokenAction.mockRejectedValueOnce(new TypeError(`Failed to fetch ${CODE}`));

    await submitCode(user, CODE);

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/could not reach Acme Shop/i);
    expect(alert).not.toHaveTextContent(CODE);
    expect(screen.getByLabelText('Authenticator code')).toHaveValue('');
  });

  it('shows no raw server text for an unknown failure', async () => {
    const user = setupUser();
    await openCodeStep(user);
    mocks.verifyTokenAction.mockResolvedValueOnce({
      success: false,
      error: 'postgres://user:pw@host/db',
    });

    await submitCode(user, CODE);

    expect(await screen.findByRole('alert')).not.toHaveTextContent(/postgres/);
  });

  it('checks the code once when the form is submitted twice before the first answer', async () => {
    const user = setupUser();
    const field = await openCodeStep(user);
    mocks.verifyTokenAction.mockReturnValueOnce(new Promise(() => {}));
    await user.type(field, CODE);
    const form = field.closest('form') as HTMLFormElement;

    // Two submits in the same tick: only the ref guard can stop the second one.
    fireEvent.submit(form);
    fireEvent.submit(form);

    // One call for the password step, one for the code.
    expect(mocks.verifyTokenAction).toHaveBeenCalledTimes(2);
    const button = await screen.findByRole('button', { name: /verifying/i });
    expect(button).toBeDisabled();
    expect(form).toHaveAttribute('aria-busy', 'true');
  });

  it('Back returns to the sign-in form with the email kept, focus on the password, signed out once', async () => {
    const user = setupUser();
    await openCodeStep(user);

    await user.click(screen.getByRole('button', { name: /back to sign in/i }));

    expect(screen.queryByLabelText('Authenticator code')).not.toBeInTheDocument();
    expect(screen.getByLabelText(/email/i)).toHaveValue('a@b.com');
    expect(screen.getByLabelText(/email/i)).toBeVisible();
    await waitFor(() => expect(screen.getByLabelText(/^password$/i)).toHaveFocus());
    expect(screen.getByLabelText(/^password$/i)).toHaveValue('');
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(mocks.signOut).toHaveBeenCalledTimes(1);
  });

  it('signs in again after Back with a new ID token, with no old alert or code', async () => {
    const user = setupUser();
    await openCodeStep(user);
    mocks.verifyTokenAction.mockResolvedValueOnce(totpInvalid);
    await submitCode(user, '000000');
    await screen.findByRole('alert');
    await user.click(screen.getByRole('button', { name: /back to sign in/i }));
    mocks.signInWithEmail.mockResolvedValue({ user: {}, idToken: 'id-token-2', error: null });
    mocks.verifyTokenAction.mockResolvedValueOnce(totpRequired);

    await user.type(screen.getByLabelText(/^password$/i), 'secret123');
    await user.click(screen.getByRole('button', { name: /^sign in$/i }));

    const field = await screen.findByLabelText('Authenticator code');
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(field).toHaveValue('');
    mocks.verifyTokenAction.mockResolvedValueOnce({ success: true, data: {} });
    await submitCode(user, CODE);
    expect(mocks.verifyTokenAction).toHaveBeenLastCalledWith('id-token-2', CODE);
  });

  it('hides the provider buttons and the password form while the code step is open', async () => {
    const user = setupUser();
    await openCodeStep(user);

    expect(screen.getByLabelText(/email/i)).not.toBeVisible();
    expect(screen.getByRole('button', { name: /google/i, hidden: true })).not.toBeVisible();
  });

  it('offers a support link for someone who cannot get a code', async () => {
    const user = setupUser();
    await openCodeStep(user);

    expect(screen.getByRole('link', { name: /contact support/i })).toHaveAttribute(
      'href',
      '/contact'
    );
  });

  it('opens from a provider sign-in too, with the same ID token', async () => {
    const user = setupUser();
    mocks.signInWithGoogle.mockResolvedValue({ user: {}, idToken: ID_TOKEN, error: null });
    mocks.verifyTokenAction.mockResolvedValueOnce(totpRequired);
    render(<LoginPanel />);

    await user.click(screen.getByRole('button', { name: /google/i }));

    expect(await screen.findByLabelText('Authenticator code')).toBeInTheDocument();
    expect(mocks.toast).not.toHaveBeenCalled();
    mocks.verifyTokenAction.mockResolvedValueOnce({ success: true, data: {} });
    await submitCode(user, CODE);
    expect(mocks.verifyTokenAction).toHaveBeenLastCalledWith(ID_TOKEN, CODE);
  });
});

describe('TotpChallengeForm', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.signOut.mockResolvedValue({ error: null });
  });
  afterEach(async () => {
    cleanup();
    // The step's deferred abandon check runs on a zero-delay timer: let it settle before the next test.
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

  it('calls onSignedIn after a successful code, submitted with Enter, and stays locked', async () => {
    const user = setupUser();
    const onSignedIn = vi.fn();
    mocks.verifyTokenAction.mockResolvedValue({ success: true, data: {} });
    render(<TotpChallengeForm idToken={ID_TOKEN} onSignedIn={onSignedIn} onCancel={vi.fn()} />);

    await user.type(screen.getByLabelText('Authenticator code'), CODE);
    await user.keyboard('{Enter}');

    await waitFor(() => expect(onSignedIn).toHaveBeenCalledTimes(1));
    expect(mocks.verifyTokenAction).toHaveBeenCalledWith(ID_TOKEN, CODE);
    expect(screen.getByRole('button', { name: /verifying/i })).toBeDisabled();
  });

  it('calls onCancel from Back, after signing out', async () => {
    const user = setupUser();
    const onCancel = vi.fn();
    render(<TotpChallengeForm idToken={ID_TOKEN} onSignedIn={vi.fn()} onCancel={onCancel} />);

    await user.click(screen.getByRole('button', { name: /back to sign in/i }));

    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(mocks.signOut).toHaveBeenCalledTimes(1);
  });

  describe('abandoning the step', () => {
    beforeEach(() => vi.useFakeTimers());
    afterEach(() => vi.useRealTimers());

    it('signs out when it is unmounted without a successful sign-in', () => {
      const { unmount } = render(
        <TotpChallengeForm idToken={ID_TOKEN} onSignedIn={vi.fn()} onCancel={vi.fn()} />
      );

      unmount();
      act(() => vi.runAllTimers());

      expect(mocks.signOut).toHaveBeenCalledTimes(1);
    });

    it('does not sign out on the StrictMode mount, simulated unmount and remount', () => {
      const { unmount } = render(
        <StrictMode>
          <TotpChallengeForm idToken={ID_TOKEN} onSignedIn={vi.fn()} onCancel={vi.fn()} />
        </StrictMode>
      );

      act(() => vi.runAllTimers());
      expect(mocks.signOut).not.toHaveBeenCalled();

      unmount();
      act(() => vi.runAllTimers());
      expect(mocks.signOut).toHaveBeenCalledTimes(1);
    });

    it('does not throw to the UI when signing out fails', () => {
      mocks.signOut.mockRejectedValue(new Error('network'));
      const { unmount } = render(
        <TotpChallengeForm idToken={ID_TOKEN} onSignedIn={vi.fn()} onCancel={vi.fn()} />
      );

      expect(() => {
        unmount();
        act(() => vi.runAllTimers());
      }).not.toThrow();
      expect(mocks.signOut).toHaveBeenCalledTimes(1);
    });

    it('keeps the Firebase user, and does not resend the code, when Back is used after a stalled redirect', async () => {
      const onCancel = vi.fn();
      mocks.verifyTokenAction.mockResolvedValue({ success: true, data: {} });
      render(<TotpChallengeForm idToken={ID_TOKEN} onSignedIn={vi.fn()} onCancel={onCancel} />);
      const field = screen.getByLabelText('Authenticator code');
      fireEvent.change(field, { target: { value: CODE } });
      await act(async () => {
        fireEvent.submit(field.closest('form') as HTMLFormElement);
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(REDIRECT_RECOVERY_MS);
      });

      // The form is handed back (the redirect stalled) but the session cookie is already set.
      fireEvent.submit(field.closest('form') as HTMLFormElement);
      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: /back to sign in/i }));
      });

      expect(mocks.verifyTokenAction).toHaveBeenCalledTimes(1);
      expect(mocks.signOut).not.toHaveBeenCalled();
      expect(onCancel).toHaveBeenCalledTimes(1);
    });

    it('is a post form, so a native submit can never put the code in the address', () => {
      render(<TotpChallengeForm idToken={ID_TOKEN} onSignedIn={vi.fn()} onCancel={vi.fn()} />);

      expect(screen.getByLabelText('Authenticator code').closest('form')).toHaveAttribute(
        'method',
        'post'
      );
    });
  });

  it('keeps the Firebase sign-in after a successful code, even when unmounted', async () => {
    const user = setupUser();
    const onSignedIn = vi.fn();
    mocks.verifyTokenAction.mockResolvedValue({ success: true, data: {} });
    const { unmount } = render(
      <TotpChallengeForm idToken={ID_TOKEN} onSignedIn={onSignedIn} onCancel={vi.fn()} />
    );
    await user.type(screen.getByLabelText('Authenticator code'), CODE);
    await user.keyboard('{Enter}');
    await waitFor(() => expect(onSignedIn).toHaveBeenCalledTimes(1));

    vi.useFakeTimers();
    unmount();
    act(() => vi.runAllTimers());
    vi.useRealTimers();

    expect(mocks.signOut).not.toHaveBeenCalled();
  });
});
