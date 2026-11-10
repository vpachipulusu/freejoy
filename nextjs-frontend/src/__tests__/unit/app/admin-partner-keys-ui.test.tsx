/**
 * The partner keys UI on the merchant page: the keys list, the show-once create dialog (read-only field, copy
 * button, status announcement, key never in a toast), and the revoke confirmation that names the key by label.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const API_KEY = `fj_test_${'k'.repeat(24)}_${'0'.repeat(64)}`;
const KEY_ID = 'k'.repeat(24);

const mocks = vi.hoisted(() => ({
  createPartnerKeyAction: vi.fn(),
  revokePartnerKeyAction: vi.fn(),
  fetchAdminPartnerKeys: vi.fn(),
  toast: vi.fn(),
  refresh: vi.fn(),
  logError: vi.fn(),
}));

vi.mock('@/app/actions/admin/partner-key-actions', () => ({
  createPartnerKeyAction: mocks.createPartnerKeyAction,
  revokePartnerKeyAction: mocks.revokePartnerKeyAction,
}));
vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: mocks.toast }) }));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ refresh: mocks.refresh, push: vi.fn() }),
}));
vi.mock('@/lib/logger', () => ({
  logger: { info: vi.fn(), warn: vi.fn(), error: mocks.logError },
}));
vi.mock('@/lib/api/admin-partner-keys', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api/admin-partner-keys')>(
    '@/lib/api/admin-partner-keys'
  );
  return { ...actual, fetchAdminPartnerKeys: mocks.fetchAdminPartnerKeys };
});

import {
  CreatePartnerKeyDialog,
  KEY_COPIED_ANNOUNCEMENT,
  KEY_COPY_FAILED_ANNOUNCEMENT,
  KEY_CREATED_ANNOUNCEMENT,
} from '@/app/(admin)/admin/merchants/_components/create-partner-key-dialog';
import { RevokePartnerKeyButton } from '@/app/(admin)/admin/merchants/_components/revoke-partner-key-button';
import {
  INACTIVE_NOTICE,
  issueBlocker,
  NO_WEBSITE_NOTICE,
  PartnerKeysSection,
} from '@/app/(admin)/admin/merchants/_components/partner-keys-section';

const ISSUED = {
  success: true as const,
  data: {
    apiKey: API_KEY,
    key: {
      id: KEY_ID,
      label: 'Feed',
      createdAt: '2026-01-01T00:00:00Z',
      lastUsedAt: null,
      revokedAt: null,
    },
  },
};

function record(overrides: Record<string, unknown> = {}) {
  return {
    id: KEY_ID,
    merchant_id: 'm1',
    label: 'Feed',
    created_at: '2026-01-01T10:00:00Z',
    last_used_at: null,
    revoked_at: null,
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.createPartnerKeyAction.mockResolvedValue(ISSUED);
  mocks.revokePartnerKeyAction.mockResolvedValue({ success: true, data: {} });
});

async function openCreate(user: ReturnType<typeof userEvent.setup>) {
  render(<CreatePartnerKeyDialog merchantId="m1" merchantName="Acme" />);
  await user.click(screen.getByRole('button', { name: 'Create key' }));
  return screen.findByRole('dialog');
}

describe('CreatePartnerKeyDialog', () => {
  it('opens a labelled dialog with a label field', async () => {
    const user = userEvent.setup();

    const dialog = await openCreate(user);

    expect(dialog).toHaveAccessibleName('Create a partner key');
    expect(screen.getByLabelText(/Label/)).toBeInTheDocument();
  });

  it('closes on Escape without calling the backend', async () => {
    const user = userEvent.setup();
    await openCreate(user);

    await user.keyboard('{Escape}');

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(mocks.createPartnerKeyAction).not.toHaveBeenCalled();
    expect(mocks.refresh).not.toHaveBeenCalled();
  });

  it('shows the key once in a read-only field and announces it, never in a toast', async () => {
    const user = userEvent.setup();
    await openCreate(user);

    await user.type(screen.getByLabelText(/Label/), 'Feed');
    await user.click(screen.getByRole('button', { name: 'Create key' }));

    const field = await screen.findByLabelText('New partner API key');
    expect(field).toHaveValue(API_KEY);
    expect(field).toHaveAttribute('readonly');
    expect(screen.getByRole('status')).toHaveTextContent(KEY_CREATED_ANNOUNCEMENT);
    expect(screen.getByRole('dialog')).toHaveAccessibleName('Your new key');
    expect(mocks.createPartnerKeyAction).toHaveBeenCalledWith('m1', { label: 'Feed' });
    expect(JSON.stringify(mocks.toast.mock.calls)).not.toContain(API_KEY);
    expect(mocks.toast).not.toHaveBeenCalled();
  });

  it('copies the key and announces it', async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    await openCreate(user);
    await user.type(screen.getByLabelText(/Label/), 'Feed');
    await user.click(screen.getByRole('button', { name: 'Create key' }));
    await screen.findByLabelText('New partner API key');

    await user.click(screen.getByRole('button', { name: 'Copy key' }));

    expect(writeText).toHaveBeenCalledWith(API_KEY);
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent(KEY_COPIED_ANNOUNCEMENT)
    );
  });

  it('says so when the clipboard is not available and leaves the key selected', async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockRejectedValue(new Error('denied'));
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    await openCreate(user);
    await user.type(screen.getByLabelText(/Label/), 'Feed');
    await user.click(screen.getByRole('button', { name: 'Create key' }));
    await screen.findByLabelText('New partner API key');

    await user.click(screen.getByRole('button', { name: 'Copy key' }));

    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent(KEY_COPY_FAILED_ANNOUNCEMENT)
    );
  });

  it('forgets the key when the dialog closes and refreshes the page', async () => {
    const user = userEvent.setup();
    await openCreate(user);
    await user.type(screen.getByLabelText(/Label/), 'Feed');
    await user.click(screen.getByRole('button', { name: 'Create key' }));
    await screen.findByLabelText('New partner API key');

    await user.click(screen.getByRole('button', { name: 'Done' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(document.body.innerHTML).not.toContain(API_KEY);
    expect(mocks.refresh).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole('button', { name: 'Create key' }));
    expect(screen.queryByLabelText('New partner API key')).not.toBeInTheDocument();
  });

  it('does not close when the overlay is clicked while the key is on screen', async () => {
    const user = userEvent.setup();
    await openCreate(user);
    await user.type(screen.getByLabelText(/Label/), 'Feed');
    await user.click(screen.getByRole('button', { name: 'Create key' }));
    await screen.findByLabelText('New partner API key');

    const overlay = document.querySelector('[class*="inset-0"]') as HTMLElement;
    await userEvent.setup({ pointerEventsCheck: 0 }).click(overlay);

    expect(screen.getByLabelText('New partner API key')).toBeInTheDocument();
  });

  it('shows a field message for a refused label and keeps the dialog open', async () => {
    const user = userEvent.setup();
    mocks.createPartnerKeyAction.mockResolvedValue({
      success: false,
      error: 'Check the highlighted fields and try again.',
      fieldErrors: { label: 'Enter a label, for example "Production feed".' },
    });
    await openCreate(user);

    await user.click(screen.getByRole('button', { name: 'Create key' }));

    expect(await screen.findByText(/Enter a label/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Label/)).toHaveAttribute('aria-invalid', 'true');
    expect(screen.queryByLabelText('New partner API key')).not.toBeInTheDocument();
  });

  it('shows the curated message of a refusal in an alert', async () => {
    const user = userEvent.setup();
    mocks.createPartnerKeyAction.mockResolvedValue({
      success: false,
      error: 'Add the merchant website before issuing a key.',
    });
    await openCreate(user);
    await user.type(screen.getByLabelText(/Label/), 'Feed');

    await user.click(screen.getByRole('button', { name: 'Create key' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Add the merchant website before issuing a key.'
    );
  });

  it('turns a thrown action into a message', async () => {
    const user = userEvent.setup();
    mocks.createPartnerKeyAction.mockRejectedValue(new Error('network'));
    await openCreate(user);
    await user.type(screen.getByLabelText(/Label/), 'Feed');

    await user.click(screen.getByRole('button', { name: 'Create key' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Something went wrong');
  });
});

describe('RevokePartnerKeyButton', () => {
  it('asks first, naming the key by its label, and does nothing on cancel', async () => {
    const user = userEvent.setup();
    render(<RevokePartnerKeyButton merchantId="m1" keyId={KEY_ID} label="Feed" />);

    await user.click(screen.getByRole('button', { name: 'Revoke key Feed' }));

    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveAccessibleName('Revoke this key?');
    expect(dialog).toHaveTextContent('Feed');
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(mocks.revokePartnerKeyAction).not.toHaveBeenCalled();
  });

  it('revokes on confirmation, announces it with the label only and refreshes', async () => {
    const user = userEvent.setup();
    render(<RevokePartnerKeyButton merchantId="m1" keyId={KEY_ID} label="Feed" />);

    await user.click(screen.getByRole('button', { name: 'Revoke key Feed' }));
    await user.click(await screen.findByRole('button', { name: 'Revoke key' }));

    await waitFor(() => expect(mocks.revokePartnerKeyAction).toHaveBeenCalledWith('m1', KEY_ID));
    await waitFor(() =>
      expect(mocks.toast).toHaveBeenCalledWith({ title: 'Key revoked', description: 'Feed' })
    );
    expect(mocks.refresh).toHaveBeenCalled();
  });

  it('reports a refusal with the curated message', async () => {
    const user = userEvent.setup();
    mocks.revokePartnerKeyAction.mockResolvedValue({ success: false, error: 'Not allowed.' });
    render(<RevokePartnerKeyButton merchantId="m1" keyId={KEY_ID} label="Feed" />);

    await user.click(screen.getByRole('button', { name: 'Revoke key Feed' }));
    await user.click(await screen.findByRole('button', { name: 'Revoke key' }));

    await waitFor(() =>
      expect(mocks.toast).toHaveBeenCalledWith({
        variant: 'destructive',
        title: 'Could not revoke the key',
        description: 'Not allowed.',
      })
    );
  });

  it('closes on Escape', async () => {
    const user = userEvent.setup();
    render(<RevokePartnerKeyButton merchantId="m1" keyId={KEY_ID} label="Feed" />);
    await user.click(screen.getByRole('button', { name: 'Revoke key Feed' }));
    await screen.findByRole('dialog');

    await user.keyboard('{Escape}');

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });
});

describe('PartnerKeysSection', () => {
  async function renderSection(
    props: { website: string | null; active: boolean } = {
      website: 'https://shop.example.com',
      active: true,
    }
  ) {
    render(await PartnerKeysSection({ merchantId: 'm1', merchantName: 'Acme', ...props }));
  }

  it('lists label, created, last used and status, with Revoke on active keys only', async () => {
    mocks.fetchAdminPartnerKeys.mockResolvedValue({
      success: true,
      data: [
        record({ last_used_at: '2026-02-01T10:00:00Z' }),
        record({ id: 'r'.repeat(24), label: 'Old', revoked_at: '2026-01-05T10:00:00Z' }),
      ],
    });

    await renderSection();

    expect(screen.getByRole('heading', { name: 'Partner API keys' })).toBeInTheDocument();
    expect(screen.getByText('Feed')).toBeInTheDocument();
    expect(screen.getByText('Active')).toBeInTheDocument();
    expect(screen.getByText(/^Revoked /)).toBeInTheDocument();
    expect(screen.getByText('Never')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Revoke key Feed' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Revoke key Old' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Create key' })).toBeInTheDocument();
  });

  it('never renders a key value or a hash', async () => {
    mocks.fetchAdminPartnerKeys.mockResolvedValue({ success: true, data: [record()] });

    await renderSection();

    expect(document.body.innerHTML).not.toMatch(/fj_[a-z0-9]+_[a-z0-9]{24}_/);
  });

  it('says there are no keys yet', async () => {
    mocks.fetchAdminPartnerKeys.mockResolvedValue({ success: true, data: [] });

    await renderSection();

    expect(screen.getByText('No keys yet.')).toBeInTheDocument();
  });

  it.each([
    [{ website: null, active: true }, NO_WEBSITE_NOTICE],
    [{ website: 'https://shop.example.com', active: false }, INACTIVE_NOTICE],
  ])('offers no Create key button and says why: %o', async (props, notice) => {
    mocks.fetchAdminPartnerKeys.mockResolvedValue({ success: true, data: [] });

    await renderSection(props);

    expect(screen.queryByRole('button', { name: 'Create key' })).not.toBeInTheDocument();
    expect(screen.getByText(notice)).toBeInTheDocument();
  });

  it('shows a curated message when the keys cannot be read', async () => {
    mocks.fetchAdminPartnerKeys.mockResolvedValue({
      success: false,
      error: 'SECRET backend text',
      status: 403,
    });

    await renderSection();

    expect(screen.getByRole('alert')).toHaveTextContent('You do not have permission to do that');
    expect(document.body.innerHTML).not.toContain('SECRET');
  });

  it('logs an unexpected failure by status only', async () => {
    mocks.fetchAdminPartnerKeys.mockResolvedValue({ success: false, error: 'boom', status: 500 });

    await renderSection();

    expect(mocks.logError).toHaveBeenCalledWith('Admin merchant page: partner keys failed', {
      status: 500,
      code: undefined,
    });
  });
});

describe('issueBlocker', () => {
  it('prefers the inactive notice, then the website one, else nothing', () => {
    expect(issueBlocker(null, false)).toBe(INACTIVE_NOTICE);
    expect(issueBlocker(null, true)).toBe(NO_WEBSITE_NOTICE);
    expect(issueBlocker('https://x.example', true)).toBeNull();
  });
});
