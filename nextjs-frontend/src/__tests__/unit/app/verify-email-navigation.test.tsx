import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, waitFor } from '@testing-library/react';
import { recordNavigationAnnouncements } from '@/__tests__/unit/helpers/navigation-announcements';

/** the verify-email page tells the loading indicator about each redirect it makes, and about no other. */

const mocks = vi.hoisted(() => ({
  push: vi.fn(),
  onAuthStateChanged: vi.fn(),
  refreshSessionAction: vi.fn(),
}));

// one router object: the page's effect depends on it, and a new one per render would subscribe again
vi.mock('next/navigation', () => {
  const router = { push: mocks.push };
  return { useRouter: () => router };
});
vi.mock('next/link', () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}));
vi.mock('@/lib/firebase/client', () => ({ auth: { currentUser: null } }));
vi.mock('firebase/auth', () => ({ onAuthStateChanged: mocks.onAuthStateChanged }));
vi.mock('@/lib/firebase/auth-helpers', () => ({ resendVerificationEmail: vi.fn() }));
vi.mock('@/app/actions/auth-actions', () => ({ refreshSessionAction: mocks.refreshSessionAction }));

import VerifyEmailPage from '@/app/(auth)/verify-email/page';

function signInAs(user: unknown) {
  mocks.onAuthStateChanged.mockImplementation(
    (_auth: unknown, callback: (user: unknown) => void) => {
      callback(user);
      return () => undefined;
    }
  );
}

const verifiedUser = {
  email: 'a@example.com',
  emailVerified: true,
  getIdToken: async () => 'token',
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe('VerifyEmailPage navigation', () => {
  it('announces the redirect to /login when nobody is signed in', () => {
    const announced = recordNavigationAnnouncements();
    signInAs(null);

    render(<VerifyEmailPage />);

    expect(mocks.push).toHaveBeenCalledWith('/login');
    expect(announced.hrefs).toEqual(['/login']);
  });

  it('announces the redirect to /dashboard once the verified claim is in the session', async () => {
    const announced = recordNavigationAnnouncements();
    mocks.refreshSessionAction.mockResolvedValue({
      success: true,
      data: { claimsEmailVerified: true },
    });
    signInAs(verifiedUser);

    render(<VerifyEmailPage />);

    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith('/dashboard'));
    expect(announced.hrefs).toEqual(['/dashboard']);
  });

  it('announces the redirect to /login when the session must be signed in again', async () => {
    const announced = recordNavigationAnnouncements();
    mocks.refreshSessionAction.mockResolvedValue({
      success: false,
      data: { signInRequired: true },
    });
    signInAs(verifiedUser);

    render(<VerifyEmailPage />);

    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith('/login'));
    expect(announced.hrefs).toEqual(['/login']);
  });

  it('announces nothing when the verified email cannot be confirmed yet', async () => {
    const announced = recordNavigationAnnouncements();
    mocks.refreshSessionAction.mockResolvedValue({ success: false, data: {} });
    signInAs(verifiedUser);

    render(<VerifyEmailPage />);

    await waitFor(() => expect(mocks.refreshSessionAction).toHaveBeenCalled());
    expect(mocks.push).not.toHaveBeenCalled();
    expect(announced.hrefs).toEqual([]);
  });
});
