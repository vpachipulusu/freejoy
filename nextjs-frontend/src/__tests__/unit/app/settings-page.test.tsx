import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { setupUser } from '@/__tests__/unit/helpers/user';

const mocks = vi.hoisted(() => ({
  getUserSettingsAction: vi.fn(),
  updateUserSettingsAction: vi.fn(),
  toast: vi.fn(),
}));

vi.mock('next/navigation', () => ({ useRouter: () => ({ refresh: vi.fn(), push: vi.fn() }) }));
vi.mock('@/app/actions/user-actions', () => ({
  getUserSettingsAction: mocks.getUserSettingsAction,
  updateUserSettingsAction: mocks.updateUserSettingsAction,
}));
vi.mock('@/app/actions/connect-actions', () => ({
  getConnectStatusAction: vi.fn().mockResolvedValue({
    success: true,
    data: {
      hasAccount: false,
      chargesEnabled: false,
      payoutsEnabled: false,
      detailsSubmitted: false,
    },
  }),
  startConnectOnboardingAction: vi.fn(),
}));
vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: mocks.toast }) }));

import SettingsPage from '@/app/dashboard/settings/page';

const SETTINGS = { theme: 'dark', emailNotifications: true, language: 'fr', timezone: 'UTC' };

beforeEach(() => {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  );
  vi.clearAllMocks();
  mocks.getUserSettingsAction.mockResolvedValue({ success: true, data: { settings: SETTINGS } });
});

describe('SettingsPage structure', () => {
  it('renders exactly one h1 with each section as an h2', async () => {
    render(<SettingsPage />);

    expect(await screen.findByRole('heading', { level: 1, name: 'Settings' })).toBeInTheDocument();
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);
    for (const name of ['Appearance', 'Notifications', 'Regional Settings']) {
      expect(screen.getByRole('heading', { level: 2, name })).toBeInTheDocument();
    }
  });

  it('marks the page busy while settings load', () => {
    mocks.getUserSettingsAction.mockReturnValue(new Promise(() => {}));

    render(<SettingsPage />);

    const status = screen.getByRole('status');
    expect(status).toHaveTextContent(/loading settings/i);
    expect(status.parentElement).toHaveAttribute('aria-busy', 'true');
  });

  it('offers a retry instead of the form when loading fails', async () => {
    mocks.getUserSettingsAction.mockResolvedValueOnce({ success: false, error: 'down' });
    const user = setupUser();

    render(<SettingsPage />);

    expect(await screen.findByRole('alert')).toHaveTextContent(/could not load your settings/i);
    expect(screen.queryByRole('button', { name: /save settings/i })).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /try again/i }));

    expect(await screen.findByRole('combobox', { name: 'Language' })).toHaveTextContent('Français');
  });

  it('gives every control an accessible name and a linked hint', async () => {
    render(<SettingsPage />);

    const theme = await screen.findByRole('combobox', { name: 'Theme' });
    expect(theme).toHaveAccessibleDescription(/preferred color theme/i);
    expect(screen.getByRole('combobox', { name: 'Language' })).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: 'Timezone' })).toBeInTheDocument();
    expect(screen.getByRole('switch', { name: 'Email Notifications' })).toBeChecked();
  });
});

describe('SettingsPage saving', () => {
  it('shows a Saving label while pending, then a success toast', async () => {
    let finish: (value: { success: boolean }) => void = () => {};
    mocks.updateUserSettingsAction.mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      })
    );
    const user = setupUser();

    render(<SettingsPage />);
    await user.click(await screen.findByRole('button', { name: /save settings/i }));

    expect(await screen.findByRole('button', { name: /saving/i })).toBeDisabled();
    expect(mocks.toast).not.toHaveBeenCalled();

    finish({ success: true });

    expect(await screen.findByRole('button', { name: /save settings/i })).toBeEnabled();
    expect(mocks.toast).toHaveBeenCalledWith(
      expect.objectContaining({ description: expect.stringMatching(/settings updated/i) })
    );
    expect(mocks.updateUserSettingsAction).toHaveBeenCalledWith(SETTINGS);
  });

  it('shows a destructive toast when saving fails', async () => {
    mocks.updateUserSettingsAction.mockResolvedValue({ success: false, error: 'Nope' });
    const user = setupUser();

    render(<SettingsPage />);
    await user.click(await screen.findByRole('button', { name: /save settings/i }));

    await waitFor(() =>
      expect(mocks.toast).toHaveBeenCalledWith(
        expect.objectContaining({ variant: 'destructive', description: 'Nope' })
      )
    );
  });
});

describe('SettingsPage reset', () => {
  it('restores the saved values after Reset', async () => {
    const user = setupUser();

    render(<SettingsPage />);
    const language = await screen.findByRole('combobox', { name: 'Language' });
    expect(language).toHaveTextContent('Français');

    await user.click(screen.getByRole('switch', { name: 'Email Notifications' }));
    expect(screen.getByRole('switch', { name: 'Email Notifications' })).not.toBeChecked();

    await user.click(screen.getByRole('button', { name: /reset/i }));

    expect(screen.getByRole('switch', { name: 'Email Notifications' })).toBeChecked();
    expect(screen.getByRole('combobox', { name: 'Language' })).toHaveTextContent('Français');
  });
});
