import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { setupUser } from '@/__tests__/unit/helpers/user';

const mocks = vi.hoisted(() => ({
  getUserProfileAction: vi.fn(),
  updateUserProfileAction: vi.fn(),
  toast: vi.fn(),
}));

vi.mock('@/app/actions/user-actions', () => ({
  getUserProfileAction: mocks.getUserProfileAction,
  updateUserProfileAction: mocks.updateUserProfileAction,
  updateUserAvatarAction: vi.fn(),
}));
vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: mocks.toast }) }));

import ProfilePage from '@/app/dashboard/profile/page';

const PROFILE_USER = {
  email: 'person@example.test',
  displayName: 'Person',
  bio: '',
  location: '',
  website: '',
  phoneNumber: '',
  avatarUrl: '',
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe('ProfilePage structure', () => {
  it('renders exactly one h1 once loaded, with each card as an h2', async () => {
    mocks.getUserProfileAction.mockResolvedValue({ success: true, data: { user: PROFILE_USER } });

    render(<ProfilePage />);

    expect(await screen.findByRole('heading', { level: 1, name: 'Profile' })).toBeInTheDocument();
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);
    expect(screen.getByRole('heading', { level: 2, name: 'Profile Picture' })).toBeInTheDocument();
    expect(
      screen.getByRole('heading', { level: 2, name: 'Personal Information' })
    ).toBeInTheDocument();
  });

  it('marks the page busy while the profile loads', () => {
    mocks.getUserProfileAction.mockReturnValue(new Promise(() => {}));

    render(<ProfilePage />);

    const status = screen.getByRole('status');
    expect(status).toHaveTextContent(/loading profile/i);
    expect(status.parentElement).toHaveAttribute('aria-busy', 'true');
  });

  it('offers a retry instead of the form when loading fails', async () => {
    mocks.getUserProfileAction.mockResolvedValueOnce({ success: false, error: 'down' });
    mocks.getUserProfileAction.mockResolvedValueOnce({
      success: true,
      data: { user: PROFILE_USER },
    });
    const user = setupUser();

    render(<ProfilePage />);

    expect(await screen.findByRole('alert')).toHaveTextContent(/could not load your profile/i);
    expect(screen.queryByRole('button', { name: /save changes/i })).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /try again/i }));

    expect(await screen.findByLabelText('Email')).toHaveValue(PROFILE_USER.email);
  });

  it('keeps saved values when Cancel is pressed after a save', async () => {
    mocks.getUserProfileAction.mockResolvedValue({ success: true, data: { user: PROFILE_USER } });
    mocks.updateUserProfileAction.mockResolvedValue({ success: true });
    const user = setupUser();

    render(<ProfilePage />);
    const location = await screen.findByLabelText('Location');
    await user.type(location, 'Leeds');
    await user.click(screen.getByRole('button', { name: /save changes/i }));
    await waitFor(() => expect(mocks.updateUserProfileAction).toHaveBeenCalled());
    await user.click(await screen.findByRole('button', { name: /cancel/i }));

    expect(screen.getByLabelText('Location')).toHaveValue('Leeds');
  });

  it('links the read-only email hint to the email input', async () => {
    mocks.getUserProfileAction.mockResolvedValue({ success: true, data: { user: PROFILE_USER } });

    render(<ProfilePage />);

    const email = await screen.findByLabelText('Email');
    expect(email).toHaveAccessibleDescription(/cannot be changed/i);
  });

  it('offers the photo upload as a keyboard-reachable button backed by a labelled file input', async () => {
    mocks.getUserProfileAction.mockResolvedValue({ success: true, data: { user: PROFILE_USER } });

    render(<ProfilePage />);

    expect(await screen.findByRole('button', { name: /upload photo/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/choose a profile photo/i)).toHaveAttribute('type', 'file');
  });
});

describe('ProfilePage saving', () => {
  it('shows a Saving label while the request is pending, then a success toast', async () => {
    mocks.getUserProfileAction.mockResolvedValue({ success: true, data: { user: PROFILE_USER } });
    let finish: (value: { success: boolean }) => void = () => {};
    mocks.updateUserProfileAction.mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      })
    );
    const user = setupUser();

    render(<ProfilePage />);
    await user.click(await screen.findByRole('button', { name: /save changes/i }));

    expect(await screen.findByRole('button', { name: /saving/i })).toBeDisabled();
    expect(mocks.toast).not.toHaveBeenCalled();

    finish({ success: true });

    expect(await screen.findByRole('button', { name: /save changes/i })).toBeEnabled();
    expect(mocks.toast).toHaveBeenCalledWith(
      expect.objectContaining({ description: expect.stringMatching(/profile updated/i) })
    );
  });

  it('shows a destructive toast and restores the button when saving fails', async () => {
    mocks.getUserProfileAction.mockResolvedValue({ success: true, data: { user: PROFILE_USER } });
    mocks.updateUserProfileAction.mockResolvedValue({ success: false, error: 'Server said no' });
    const user = setupUser();

    render(<ProfilePage />);
    await user.click(await screen.findByRole('button', { name: /save changes/i }));

    await waitFor(() =>
      expect(mocks.toast).toHaveBeenCalledWith(
        expect.objectContaining({ variant: 'destructive', description: 'Server said no' })
      )
    );
    expect(screen.getByRole('button', { name: /save changes/i })).toBeEnabled();
  });

  it('links a display name error to its input', async () => {
    mocks.getUserProfileAction.mockResolvedValue({ success: true, data: { user: PROFILE_USER } });
    const user = setupUser();

    render(<ProfilePage />);
    const name = await screen.findByLabelText('Display Name');
    await user.clear(name);
    await user.type(name, 'A');
    await user.click(screen.getByRole('button', { name: /save changes/i }));

    expect(await screen.findByText(/at least 2 characters/i)).toBeInTheDocument();
    await waitFor(() => expect(name).toHaveAttribute('aria-invalid', 'true'));
    expect(name).toHaveAccessibleDescription(/at least 2 characters/i);
  });
});

describe('ProfilePage form labels', () => {
  it('gives every profile field an accessible name, with the email input read-only', async () => {
    mocks.getUserProfileAction.mockResolvedValue({ success: true, data: { user: PROFILE_USER } });

    render(<ProfilePage />);

    const email = await screen.findByLabelText('Email');
    expect(email).toHaveValue(PROFILE_USER.email);
    expect(email).toBeDisabled();

    for (const name of ['Display Name', 'Bio', 'Location', 'Website', 'Phone Number']) {
      expect(screen.getByLabelText(name)).toBeInTheDocument();
    }
  });
});

describe('ProfilePage website validation', () => {
  it('shows the application message for an invalid website URL instead of native validation', async () => {
    mocks.getUserProfileAction.mockResolvedValue({ success: true, data: { user: PROFILE_USER } });
    const user = setupUser();

    render(<ProfilePage />);

    const website = await screen.findByLabelText('Website');
    await user.type(website, 'not-a-url');
    await user.click(screen.getByRole('button', { name: /save changes/i }));

    expect(await screen.findByText('Must be a valid URL')).toBeInTheDocument();
    await waitFor(() => expect(website).toHaveAttribute('aria-invalid', 'true'));
    expect(website).toHaveAccessibleDescription(/valid url/i);
  });
});
