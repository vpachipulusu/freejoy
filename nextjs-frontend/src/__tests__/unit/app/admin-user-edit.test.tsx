/**
 * The Edit User screen: the profile form calls the update action with only the five profile fields (blank means
 * clear), validates before calling, and shows the outcome; the page reads the user and guards admin access.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { setupUser } from '@/__tests__/unit/helpers/user';
import { renderToStaticMarkup } from 'react-dom/server';

const mocks = vi.hoisted(() => ({
  updateUserAction: vi.fn(),
  fetchAdminUser: vi.fn(),
  requireAdminPage: vi.fn(),
  toast: vi.fn(),
  refresh: vi.fn(),
  push: vi.fn(),
  notFound: vi.fn(() => {
    throw new Error('NEXT_NOT_FOUND');
  }),
}));

vi.mock('@/app/actions/admin/user-actions', () => ({ updateUserAction: mocks.updateUserAction }));
vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: mocks.toast }) }));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ refresh: mocks.refresh, push: mocks.push }),
  notFound: mocks.notFound,
}));
vi.mock('@/lib/api/admin-users', async () => {
  const actual =
    await vi.importActual<typeof import('@/lib/api/admin-users')>('@/lib/api/admin-users');
  return { ...actual, fetchAdminUser: mocks.fetchAdminUser };
});
vi.mock('@/lib/logger', () => ({ logger: { error: vi.fn(), info: vi.fn(), warn: vi.fn() } }));
vi.mock('@/app/(admin)/admin/_lib/require-admin-page', () => ({
  requireAdminPage: mocks.requireAdminPage,
}));

import EditAdminUserPage from '@/app/(admin)/admin/users/[id]/edit/page';
import { UserProfileForm } from '@/app/(admin)/admin/users/user-profile-form';

const INITIAL = {
  displayName: 'Ada',
  bio: 'Mathematician',
  location: '',
  website: '',
  phoneNumber: '',
};

beforeEach(() => {
  vi.clearAllMocks();
  mocks.updateUserAction.mockResolvedValue({ success: true, data: {} });
  mocks.requireAdminPage.mockResolvedValue(undefined);
});

describe('UserProfileForm', () => {
  it('shows the current values in labelled fields', () => {
    render(<UserProfileForm userId="user-1" initialValues={INITIAL} />);

    expect(screen.getByLabelText('Display name')).toHaveValue('Ada');
    expect(screen.getByLabelText('Bio')).toHaveValue('Mathematician');
    for (const label of ['Location', 'Website', 'Phone']) {
      expect(screen.getByLabelText(label)).toHaveValue('');
    }
  });

  it('saves the edited fields, turning blanks into null, and announces success', async () => {
    const user = setupUser();
    render(<UserProfileForm userId="user-1" initialValues={INITIAL} />);

    await user.clear(screen.getByLabelText('Display name'));
    await user.type(screen.getByLabelText('Display name'), '  Ada L  ');
    await user.clear(screen.getByLabelText('Bio'));
    await user.type(screen.getByLabelText('Website'), 'https://example.test');
    await user.type(screen.getByLabelText('Phone'), '+44 20 7946 0000');
    await user.click(screen.getByRole('button', { name: 'Save changes' }));

    await waitFor(() =>
      expect(mocks.updateUserAction).toHaveBeenCalledWith('user-1', {
        displayName: 'Ada L',
        bio: null,
        location: null,
        website: 'https://example.test',
        phoneNumber: '+44 20 7946 0000',
      })
    );
    expect(mocks.refresh).toHaveBeenCalled();
    // Announced once, by the focused "Changes saved." status; no second toast.
    const saved = await screen.findByText('Changes saved.');
    expect(mocks.toast).not.toHaveBeenCalled();
    await waitFor(() => expect(saved).toHaveFocus());
  });

  it('blocks an invalid website and phone number before calling the action', async () => {
    const user = setupUser();
    render(<UserProfileForm userId="user-1" initialValues={INITIAL} />);

    await user.type(screen.getByLabelText('Website'), 'ftp://nope');
    await user.type(screen.getByLabelText('Phone'), 'call me');
    await user.click(screen.getByRole('button', { name: 'Save changes' }));

    expect(await screen.findByText('Enter a full http:// or https:// address.')).toBeVisible();
    expect(screen.getByText(/Use 3 to 20 digits/)).toBeVisible();
    expect(mocks.updateUserAction).not.toHaveBeenCalled();
  });

  it.each(['HTTPS://x.com', 'https:x.com', 'data:text/html,x'])(
    'rejects the website %j as the backend would, without calling the action',
    async (value) => {
      const user = setupUser();
      render(<UserProfileForm userId="user-1" initialValues={INITIAL} />);

      await user.type(screen.getByLabelText('Website'), value);
      await user.click(screen.getByRole('button', { name: 'Save changes' }));

      expect(await screen.findByText('Enter a full http:// or https:// address.')).toBeVisible();
      expect(mocks.updateUserAction).not.toHaveBeenCalled();
    }
  );

  it('shows the curated refusal in the failure summary when the action fails', async () => {
    mocks.updateUserAction.mockResolvedValue({
      success: false,
      error: 'You do not have permission to do that',
    });
    const user = setupUser();
    render(<UserProfileForm userId="user-1" initialValues={INITIAL} />);

    await user.type(screen.getByLabelText('Location'), 'London');
    await user.click(screen.getByRole('button', { name: 'Save changes' }));

    expect(await screen.findByText('You do not have permission to do that')).toBeVisible();
    expect(screen.getByText('Could not save')).toBeVisible();
    expect(mocks.toast).not.toHaveBeenCalled();
  });

  it('shows a generic message when the action call throws', async () => {
    mocks.updateUserAction.mockRejectedValue(new Error('network'));
    const user = setupUser();
    render(<UserProfileForm userId="user-1" initialValues={INITIAL} />);

    await user.type(screen.getByLabelText('Location'), 'London');
    await user.click(screen.getByRole('button', { name: 'Save changes' }));

    expect(await screen.findByText('Something went wrong. Please try again.')).toBeVisible();
  });
});

describe('EditAdminUserPage', () => {
  const DETAIL = {
    id: 'user-1',
    email: 'ada@example.test',
    email_verified: true,
    display_name: 'Ada',
    avatar_url: null,
    created_at: '2026-01-01T00:00:00Z',
    last_login_at: null,
    is_admin: false,
    admin_role: null,
    bio: 'Mathematician',
    location: null,
    website: null,
    phone_number: null,
    two_factor_enabled: false,
    admin_permissions: [],
  };
  const params = (id: string) => ({ params: Promise.resolve({ id }) });

  it('checks admin access before reading the user and shows the form with a way back', async () => {
    mocks.fetchAdminUser.mockResolvedValue({ success: true, data: DETAIL });

    const markup = renderToStaticMarkup((await EditAdminUserPage(params('user-1'))) as never);

    expect(mocks.requireAdminPage.mock.invocationCallOrder[0]).toBeLessThan(
      mocks.fetchAdminUser.mock.invocationCallOrder[0]
    );
    expect(markup).toContain('Edit user');
    expect(markup).toContain('ada@example.test');
    expect(markup).toContain('href="/admin/users/user-1"');
    expect(markup).toContain('aria-label="Edit user profile"');
  });

  it.each(['.', '..', 'a/b', 'abc\n', '%2e%2e', '\uff41bc'])(
    'calls notFound for the id %j without reading',
    async (id) => {
      await expect(EditAdminUserPage(params(id))).rejects.toThrow('NEXT_NOT_FOUND');
      expect(mocks.fetchAdminUser).not.toHaveBeenCalled();
    }
  );

  it('does not read the user when the admin check refuses', async () => {
    mocks.requireAdminPage.mockRejectedValue(new Error('NEXT_REDIRECT'));

    await expect(EditAdminUserPage(params('user-1'))).rejects.toThrow('NEXT_REDIRECT');
    expect(mocks.fetchAdminUser).not.toHaveBeenCalled();
  });

  it('calls notFound for a missing user', async () => {
    mocks.fetchAdminUser.mockResolvedValue({ success: false, error: 'x', status: 404 });

    await expect(EditAdminUserPage(params('missing'))).rejects.toThrow('NEXT_NOT_FOUND');
  });

  it('shows a curated error with retry for other failures', async () => {
    mocks.fetchAdminUser.mockResolvedValue({ success: false, error: 'SECRET', status: 500 });

    const markup = renderToStaticMarkup((await EditAdminUserPage(params('a-b'))) as never);

    expect(markup).toContain('Something went wrong. Please try again.');
    expect(markup).not.toContain('SECRET');
    expect(markup).toContain('href="/admin/users/a-b/edit"');
  });
});
