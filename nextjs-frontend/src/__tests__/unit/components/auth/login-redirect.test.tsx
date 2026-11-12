/** FREEJ-41: the page a signed-out visitor was sent from (`?redirect=`) survives every way of signing in. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { setupUser, type TestUser } from '@/__tests__/unit/helpers/user';
import { recordNavigationAnnouncements } from '@/__tests__/unit/helpers/navigation-announcements';

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
vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: mocks.toast }) }));
vi.mock('@/lib/firebase/auth-helpers', () => ({
  signInWithEmail: mocks.signInWithEmail,
  signInWithGoogle: mocks.signInWithGoogle,
  signInWithGitHub: mocks.signInWithGitHub,
  signOut: mocks.signOut,
}));
vi.mock('@/app/actions/auth-actions', () => ({ verifyTokenAction: mocks.verifyTokenAction }));

import { LoginForm } from '@/components/auth/login-form';
import { LoginPanel } from '@/components/auth/login-panel';
import { OAuthButtons } from '@/components/auth/oauth-buttons';
import { TOTP_REQUIRED_ERROR } from '@/lib/auth/auth-error-messages';

const ID_TOKEN = 'id-token-1';
const CODE = '123456';
const TARGET = '/admin/users?page=2';
const totpRequired = {
  success: false,
  error: TOTP_REQUIRED_ERROR,
  data: { totpRequired: true },
};

/** Values that skipped the page-level check. Each must still end at /dashboard. */
const UNSAFE = [
  '//evil.com',
  'https://evil.com',
  'javascript:alert(1)',
  '/login',
  '/api/auth/clear-session',
  '/%2F/evil.com',
  '',
];

type User = TestUser;

async function submitPassword(user: User) {
  await user.type(screen.getByLabelText(/email/i), 'a@b.com');
  await user.type(screen.getByLabelText(/^password$/i), 'secret123');
  await user.click(screen.getByRole('button', { name: /^sign in$/i }));
}

async function submitCode(user: User) {
  await user.type(await screen.findByLabelText('Authenticator code'), CODE);
  await user.click(screen.getByRole('button', { name: /verify and sign in/i }));
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.signOut.mockResolvedValue({ error: null });
  mocks.signInWithEmail.mockResolvedValue({ user: {}, idToken: ID_TOKEN, error: null });
  mocks.signInWithGoogle.mockResolvedValue({ user: {}, idToken: ID_TOKEN, error: null });
  mocks.signInWithGitHub.mockResolvedValue({ user: {}, idToken: ID_TOKEN, error: null });
  mocks.verifyTokenAction.mockResolvedValue({ success: true, data: {} });
});
afterEach(async () => {
  cleanup();
  // The code step's deferred abandon check runs on a zero-delay timer: let it settle before the next test.
  await new Promise((resolve) => setTimeout(resolve, 0));
});

describe('email and password sign-in', () => {
  it('goes to the redirect target, then refreshes', async () => {
    const announced = recordNavigationAnnouncements();
    const user = setupUser();
    render(<LoginForm onTotpRequired={vi.fn()} redirectTo={TARGET} />);

    await submitPassword(user);

    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith(TARGET));
    expect(mocks.push).toHaveBeenCalledTimes(1);
    expect(announced.hrefs).toEqual([TARGET]);
    expect(mocks.refresh).toHaveBeenCalledTimes(1);
  });

  it('goes to /dashboard when there is no redirect', async () => {
    const user = setupUser();
    render(<LoginForm onTotpRequired={vi.fn()} />);

    await submitPassword(user);

    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith('/dashboard'));
  });

  it.each(UNSAFE)('goes to /dashboard for the unsafe value %j', async (unsafe) => {
    const user = setupUser();
    render(<LoginForm onTotpRequired={vi.fn()} redirectTo={unsafe} />);

    await submitPassword(user);

    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith('/dashboard'));
    expect(mocks.push).toHaveBeenCalledTimes(1);
  });

  it('does not navigate when the sign-in is refused', async () => {
    const user = setupUser();
    mocks.verifyTokenAction.mockResolvedValue({ success: false, error: 'nope' });
    render(<LoginForm onTotpRequired={vi.fn()} redirectTo={TARGET} />);

    await submitPassword(user);

    await screen.findByRole('alert');
    expect(mocks.push).not.toHaveBeenCalled();
  });
});

describe('provider sign-in', () => {
  it.each([
    ['Google', /google/i],
    ['GitHub', /github/i],
  ])('%s goes to the redirect target', async (_name, button) => {
    const user = setupUser();
    render(<OAuthButtons redirectTo={TARGET} />);

    await user.click(screen.getByRole('button', { name: button }));

    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith(TARGET));
    expect(mocks.refresh).toHaveBeenCalledTimes(1);
  });

  it('goes to /dashboard when there is no redirect (the sign-up page has none)', async () => {
    const user = setupUser();
    render(<OAuthButtons />);

    await user.click(screen.getByRole('button', { name: /google/i }));

    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith('/dashboard'));
  });

  it.each(UNSAFE)('goes to /dashboard for the unsafe value %j', async (unsafe) => {
    const user = setupUser();
    render(<OAuthButtons redirectTo={unsafe} />);

    await user.click(screen.getByRole('button', { name: /github/i }));

    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith('/dashboard'));
  });
});

describe('LoginPanel', () => {
  it('passes the redirect to the password form', async () => {
    const user = setupUser();
    render(<LoginPanel redirectTo={TARGET} />);

    await submitPassword(user);

    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith(TARGET));
  });

  it('passes the redirect to the provider buttons', async () => {
    const user = setupUser();
    render(<LoginPanel redirectTo={TARGET} />);

    await user.click(screen.getByRole('button', { name: /github/i }));

    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith(TARGET));
  });

  it('goes to /dashboard without a redirect', async () => {
    const user = setupUser();
    render(<LoginPanel />);

    await submitPassword(user);

    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith('/dashboard'));
  });

  it.each(UNSAFE)('goes to /dashboard for the unsafe value %j', async (unsafe) => {
    const user = setupUser();
    render(<LoginPanel redirectTo={unsafe} />);

    await submitPassword(user);

    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith('/dashboard'));
  });

  describe('two-factor code step', () => {
    it('keeps the redirect after a password sign-in that needs a code', async () => {
      const user = setupUser();
      mocks.verifyTokenAction.mockResolvedValueOnce(totpRequired);
      render(<LoginPanel redirectTo={TARGET} />);
      await submitPassword(user);
      expect(mocks.push).not.toHaveBeenCalled();

      await submitCode(user);

      await waitFor(() => expect(mocks.push).toHaveBeenCalledWith(TARGET));
      expect(mocks.push).toHaveBeenCalledTimes(1);
      expect(mocks.refresh).toHaveBeenCalledTimes(1);
    });

    it('keeps the redirect after a provider sign-in that needs a code', async () => {
      const user = setupUser();
      mocks.verifyTokenAction.mockResolvedValueOnce(totpRequired);
      render(<LoginPanel redirectTo={TARGET} />);
      await user.click(screen.getByRole('button', { name: /google/i }));

      await submitCode(user);

      await waitFor(() => expect(mocks.push).toHaveBeenCalledWith(TARGET));
    });

    it('keeps the redirect when the person presses Back and signs in again with a code', async () => {
      const user = setupUser();
      mocks.verifyTokenAction.mockResolvedValueOnce(totpRequired);
      render(<LoginPanel redirectTo={TARGET} />);
      await submitPassword(user);
      await user.click(await screen.findByRole('button', { name: /back to sign in/i }));
      expect(mocks.push).not.toHaveBeenCalled();

      mocks.signInWithEmail.mockResolvedValue({ user: {}, idToken: 'id-token-2', error: null });
      mocks.verifyTokenAction.mockResolvedValueOnce(totpRequired);
      await user.type(screen.getByLabelText(/^password$/i), 'secret123');
      await user.click(screen.getByRole('button', { name: /^sign in$/i }));
      await submitCode(user);

      await waitFor(() => expect(mocks.push).toHaveBeenCalledWith(TARGET));
      expect(mocks.push).toHaveBeenCalledTimes(1);
    });

    it('keeps the redirect after Back when the second sign-in needs no code', async () => {
      const user = setupUser();
      mocks.verifyTokenAction.mockResolvedValueOnce(totpRequired);
      render(<LoginPanel redirectTo={TARGET} />);
      await submitPassword(user);
      await user.click(await screen.findByRole('button', { name: /back to sign in/i }));

      await user.type(screen.getByLabelText(/^password$/i), 'secret123');
      await user.click(screen.getByRole('button', { name: /^sign in$/i }));

      await waitFor(() => expect(mocks.push).toHaveBeenCalledWith(TARGET));
    });

    it('goes to /dashboard from the code step without a redirect', async () => {
      const user = setupUser();
      mocks.verifyTokenAction.mockResolvedValueOnce(totpRequired);
      render(<LoginPanel />);
      await submitPassword(user);

      await submitCode(user);

      await waitFor(() => expect(mocks.push).toHaveBeenCalledWith('/dashboard'));
    });

    it.each(UNSAFE)(
      'goes to /dashboard from the code step for the unsafe value %j',
      async (unsafe) => {
        const user = setupUser();
        mocks.verifyTokenAction.mockResolvedValueOnce(totpRequired);
        render(<LoginPanel redirectTo={unsafe} />);
        await submitPassword(user);

        await submitCode(user);

        await waitFor(() => expect(mocks.push).toHaveBeenCalledWith('/dashboard'));
      }
    );
  });
});
