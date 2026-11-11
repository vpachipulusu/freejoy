/**
 * The partner keys UI on the merchant page: the keys list, the show-once create dialog (read-only field, copy
 * button, status announcement, key never in a toast, never lost to an early close) and the revoke confirmation
 * that names the key by label and keeps the admin informed when it fails.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { setupUser } from '@/__tests__/unit/helpers/user';

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
  KEY_MAY_EXIST_MESSAGE,
} from '@/app/(admin)/admin/merchants/_components/create-partner-key-dialog';
import { RevokePartnerKeyButton } from '@/app/(admin)/admin/merchants/_components/revoke-partner-key-button';
import { PARTNER_KEYS_HEADING_ID } from '@/app/(admin)/admin/merchants/_components/partner-keys-ids';
import {
  INACTIVE_NOTICE,
  issueBlocker,
  NO_PERMISSION_NOTICE,
  NO_WEBSITE_NOTICE,
  PartnerKeysSection,
} from '@/app/(admin)/admin/merchants/_components/partner-keys-section';
import { ACTION_FAILED_MESSAGE } from '@/lib/admin-catalogue/settle-action';

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

function deferred() {
  let settle!: (value: unknown) => void;
  const promise = new Promise((resolve) => {
    settle = resolve;
  });
  return { promise, settle };
}

const originalClipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard');

beforeEach(() => {
  vi.clearAllMocks();
  mocks.createPartnerKeyAction.mockResolvedValue(ISSUED);
  mocks.revokePartnerKeyAction.mockResolvedValue({ success: true, data: {} });
});

afterEach(() => {
  if (originalClipboard) Object.defineProperty(navigator, 'clipboard', originalClipboard);
  else Reflect.deleteProperty(navigator, 'clipboard');
});

function setClipboard(value: unknown): void {
  Object.defineProperty(navigator, 'clipboard', { value, configurable: true });
}

async function openCreate(user: ReturnType<typeof userEvent.setup>) {
  render(<CreatePartnerKeyDialog merchantId="m1" merchantName="Acme" />);
  await user.click(screen.getByRole('button', { name: 'Create key' }));
  return screen.findByRole('dialog');
}

async function createKey(user: ReturnType<typeof userEvent.setup>, label = 'Feed') {
  await user.type(screen.getByLabelText(/Label/), label);
  await user.click(screen.getByRole('button', { name: 'Create key' }));
  return screen.findByLabelText('New partner API key');
}

function overlayOf(dialog: HTMLElement): HTMLElement {
  return dialog.previousElementSibling as HTMLElement;
}

describe('CreatePartnerKeyDialog, step one', () => {
  it('opens a labelled dialog with the focus in the label field and the live regions mounted', async () => {
    const user = setupUser();

    const dialog = await openCreate(user);

    expect(dialog).toHaveAccessibleName('Create a partner key');
    expect(screen.getByLabelText(/Label/)).toHaveFocus();
    expect(within(dialog).getByRole('status')).toBeInTheDocument();
    expect(within(dialog).getByRole('alert')).toBeInTheDocument();
  });

  it('closes on Escape and on Cancel without calling the backend', async () => {
    const user = setupUser();
    await openCreate(user);

    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: 'Create key' }));
    await user.click(await screen.findByRole('button', { name: 'Cancel' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(mocks.createPartnerKeyAction).not.toHaveBeenCalled();
    expect(mocks.refresh).not.toHaveBeenCalled();
  });

  it('closes when the overlay is clicked (the control for the guard in step two)', async () => {
    const user = setupUser({ pointerEventsCheck: 0 });
    const dialog = await openCreate(user);

    await user.click(overlayOf(dialog));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('shows the refused label on the field, announces it and puts focus there', async () => {
    const user = setupUser();
    mocks.createPartnerKeyAction.mockResolvedValue({
      success: false,
      error: 'Check the highlighted fields and try again.',
      fieldErrors: { label: 'Enter a label, for example "Production feed".' },
    });
    const dialog = await openCreate(user);

    await user.click(screen.getByRole('button', { name: 'Create key' }));

    const field = screen.getByLabelText(/Label/);
    await waitFor(() => expect(field).toHaveFocus());
    expect(field).toHaveAttribute('aria-invalid', 'true');
    expect(field).toHaveAccessibleDescription(expect.stringContaining('Enter a label'));
    expect(within(dialog).getByRole('alert')).toHaveTextContent(
      'Check the highlighted fields and try again.'
    );
    expect(screen.queryByLabelText('New partner API key')).not.toBeInTheDocument();
  });

  it('shows the curated message of a refusal in the alert', async () => {
    const user = setupUser();
    mocks.createPartnerKeyAction.mockResolvedValue({
      success: false,
      error: 'Add the merchant website before issuing a key.',
    });
    const dialog = await openCreate(user);
    await user.type(screen.getByLabelText(/Label/), 'Feed');

    await user.click(screen.getByRole('button', { name: 'Create key' }));

    await waitFor(() =>
      expect(within(dialog).getByRole('alert')).toHaveTextContent(
        'Add the merchant website before issuing a key.'
      )
    );
  });

  it('says a key may have been created when the call itself failed, and refreshes the list on close', async () => {
    const user = setupUser();
    mocks.createPartnerKeyAction.mockRejectedValue(new Error('network'));
    const dialog = await openCreate(user);
    await user.type(screen.getByLabelText(/Label/), 'Feed');

    await user.click(screen.getByRole('button', { name: 'Create key' }));

    await waitFor(() =>
      expect(within(dialog).getByRole('alert')).toHaveTextContent(KEY_MAY_EXIST_MESSAGE)
    );
    expect(KEY_MAY_EXIST_MESSAGE).not.toContain(ACTION_FAILED_MESSAGE);
    await user.keyboard('{Escape}');
    await waitFor(() => expect(mocks.refresh).toHaveBeenCalledTimes(1));
  });
});

describe('CreatePartnerKeyDialog, while the key is being created', () => {
  it('stays open on Escape, the close button and Cancel, so the key is always shown', async () => {
    const user = setupUser();
    const pending = deferred();
    mocks.createPartnerKeyAction.mockReturnValue(pending.promise);
    await openCreate(user);
    await user.type(screen.getByLabelText(/Label/), 'Feed');
    await user.click(screen.getByRole('button', { name: 'Create key' }));

    await user.keyboard('{Escape}');
    await user.click(screen.getByRole('button', { name: 'Close' }));
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.getByRole('dialog')).toBeInTheDocument();

    await act(async () => pending.settle(ISSUED));
    expect(await screen.findByLabelText('New partner API key')).toHaveValue(API_KEY);
  });

  it('marks both buttons aria-disabled (not disabled) while pending', async () => {
    const user = setupUser();
    mocks.createPartnerKeyAction.mockReturnValue(deferred().promise);
    await openCreate(user);
    await user.type(screen.getByLabelText(/Label/), 'Feed');

    await user.click(screen.getByRole('button', { name: 'Create key' }));

    const submit = await screen.findByRole('button', { name: 'Creating…' });
    expect(submit).toHaveAttribute('aria-disabled', 'true');
    expect(submit).not.toBeDisabled();
    expect(screen.getByRole('button', { name: 'Cancel' })).toHaveAttribute('aria-disabled', 'true');
  });

  it('sends one request for a double submit', async () => {
    const user = setupUser();
    mocks.createPartnerKeyAction.mockReturnValue(deferred().promise);
    await openCreate(user);
    await user.type(screen.getByLabelText(/Label/), 'Feed');

    await user.keyboard('{Enter}{Enter}');
    await user.click(screen.getByRole('button', { name: /Creat/ }));

    expect(mocks.createPartnerKeyAction).toHaveBeenCalledTimes(1);
  });
});

describe('CreatePartnerKeyDialog, step two', () => {
  it('shows the key once in a read-only field with the focus on it and the once-only text as its description', async () => {
    const user = setupUser();
    const dialog = await openCreate(user);

    const field = await createKey(user);

    expect(field).toHaveValue(API_KEY);
    expect(field).toHaveAttribute('readonly');
    await waitFor(() => expect(field).toHaveFocus());
    expect(field).toHaveAccessibleDescription(expect.stringContaining('shown once'));
    expect(within(dialog).getByRole('status')).toHaveTextContent(KEY_CREATED_ANNOUNCEMENT);
    expect(dialog).toHaveAccessibleName('Your new key');
    expect(mocks.createPartnerKeyAction).toHaveBeenCalledWith('m1', { label: 'Feed' });
    expect(mocks.toast).not.toHaveBeenCalled();
    expect(JSON.stringify(mocks.toast.mock.calls)).not.toContain(API_KEY);
  });

  it('copies the key and announces it', async () => {
    const user = setupUser();
    const writeText = vi.fn().mockResolvedValue(undefined);
    await openCreate(user);
    await createKey(user);
    setClipboard({ writeText });

    await user.click(screen.getByRole('button', { name: 'Copy key' }));

    expect(writeText).toHaveBeenCalledWith(API_KEY);
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent(KEY_COPIED_ANNOUNCEMENT)
    );
  });

  it('announces a second copy again (the text changes even when the words do not)', async () => {
    const user = setupUser();
    await openCreate(user);
    await createKey(user);
    setClipboard({ writeText: vi.fn().mockResolvedValue(undefined) });
    const seen: string[] = [];

    await user.click(screen.getByRole('button', { name: 'Copy key' }));
    await waitFor(() => seen.push(screen.getByRole('status').textContent ?? ''));
    await user.click(screen.getByRole('button', { name: 'Copy key' }));
    await waitFor(() => expect(screen.getByRole('status').textContent).not.toBe(seen[0]));

    expect(screen.getByRole('status').textContent?.trim()).toBe(KEY_COPIED_ANNOUNCEMENT);
  });

  it.each([
    ['a rejected clipboard', () => ({ writeText: vi.fn().mockRejectedValue(new Error('no')) })],
    ['no clipboard at all', () => undefined],
  ])('focuses and selects the key when there is %s', async (_name, clipboard) => {
    const user = setupUser();
    await openCreate(user);
    const field = (await createKey(user)) as HTMLInputElement;
    setClipboard(clipboard());
    await user.click(screen.getByRole('button', { name: 'Copy key' }));

    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent(KEY_COPY_FAILED_ANNOUNCEMENT)
    );
    expect(field).toHaveFocus();
    expect(field.selectionStart).toBe(0);
    expect(field.selectionEnd).toBe(API_KEY.length);
  });

  it('does not close on Escape or an overlay click while the key is shown, and closes on Done', async () => {
    const user = setupUser({ pointerEventsCheck: 0 });
    const dialog = await openCreate(user);
    await createKey(user);

    await user.keyboard('{Escape}');
    await user.click(overlayOf(dialog));
    expect(screen.getByLabelText('New partner API key')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Done' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(mocks.refresh).toHaveBeenCalledTimes(1);
  });

  it('closes on the close button, forgets the key, and reopens on the label form', async () => {
    const user = setupUser();
    await openCreate(user);
    await createKey(user);

    await user.click(screen.getByRole('button', { name: 'Close' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(document.body.innerHTML).not.toContain(API_KEY);
    await user.click(screen.getByRole('button', { name: 'Create key' }));
    expect(await screen.findByLabelText(/Label/)).toHaveValue('');
    expect(screen.queryByLabelText('New partner API key')).not.toBeInTheDocument();
    expect(document.body.innerHTML).not.toContain(API_KEY);
  });
});

describe('RevokePartnerKeyButton', () => {
  function renderRevoke() {
    return render(
      <>
        <h2 id={PARTNER_KEYS_HEADING_ID} tabIndex={-1}>
          Partner API keys
        </h2>
        <RevokePartnerKeyButton merchantId="m1" keyId={KEY_ID} label="Feed" />
      </>
    );
  }

  it('asks first, naming the key by its label, and does nothing on cancel', async () => {
    const user = setupUser();
    renderRevoke();
    const trigger = screen.getByRole('button', { name: 'Revoke key Feed' });

    await user.click(trigger);
    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveAccessibleName('Revoke this key?');
    expect(dialog).toHaveTextContent('Feed');
    await user.click(screen.getByRole('button', { name: 'Cancel' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(mocks.revokePartnerKeyAction).not.toHaveBeenCalled();
    expect(trigger).toHaveFocus();
  });

  it('returns focus to the button on Escape', async () => {
    const user = setupUser();
    renderRevoke();
    const trigger = screen.getByRole('button', { name: 'Revoke key Feed' });
    await user.click(trigger);
    await screen.findByRole('dialog');

    await user.keyboard('{Escape}');

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(trigger).toHaveFocus();
  });

  it('revokes on confirmation, announces it with the label only, refreshes and focuses the heading', async () => {
    const user = setupUser();
    renderRevoke();

    await user.click(screen.getByRole('button', { name: 'Revoke key Feed' }));
    await user.click(await screen.findByRole('button', { name: 'Revoke key' }));

    await waitFor(() => expect(mocks.revokePartnerKeyAction).toHaveBeenCalledWith('m1', KEY_ID));
    await waitFor(() =>
      expect(mocks.toast).toHaveBeenCalledWith({ title: 'Key revoked', description: 'Feed' })
    );
    expect(mocks.refresh).toHaveBeenCalled();
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Partner API keys' })).toHaveFocus()
    );
  });

  it('keeps the dialog open on a failure, shows the curated message and allows a retry', async () => {
    const user = setupUser();
    mocks.revokePartnerKeyAction
      .mockResolvedValueOnce({ success: false, error: 'Too many requests. Wait a minute.' })
      .mockResolvedValueOnce({ success: true, data: {} });
    renderRevoke();
    await user.click(screen.getByRole('button', { name: 'Revoke key Feed' }));

    await user.click(await screen.findByRole('button', { name: 'Revoke key' }));

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent('Too many requests. Wait a minute.')
    );
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(mocks.toast).not.toHaveBeenCalled();
    expect(mocks.refresh).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Revoke key' }));
    await waitFor(() => expect(mocks.revokePartnerKeyAction).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('turns a thrown call into a message in the dialog', async () => {
    const user = setupUser();
    mocks.revokePartnerKeyAction.mockRejectedValue(new Error('network'));
    renderRevoke();
    await user.click(screen.getByRole('button', { name: 'Revoke key Feed' }));

    await user.click(await screen.findByRole('button', { name: 'Revoke key' }));

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent('Something went wrong')
    );
  });

  it('cannot be closed or double sent while pending, and says it is revoking', async () => {
    const user = setupUser();
    const pending = deferred();
    mocks.revokePartnerKeyAction.mockReturnValue(pending.promise);
    renderRevoke();
    await user.click(screen.getByRole('button', { name: 'Revoke key Feed' }));

    await user.click(await screen.findByRole('button', { name: 'Revoke key' }));
    const confirm = await screen.findByRole('button', { name: 'Revoking…' });
    await user.click(confirm);
    await user.keyboard('{Escape}');

    expect(confirm).toHaveAttribute('aria-disabled', 'true');
    expect(confirm).not.toBeDisabled();
    expect(screen.getByRole('status')).toHaveTextContent('Revoking the key');
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(mocks.revokePartnerKeyAction).toHaveBeenCalledTimes(1);
    await act(async () => pending.settle({ success: true, data: {} }));
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

  it('is a named table with column headers, one row per key, Revoke on active keys only', async () => {
    mocks.fetchAdminPartnerKeys.mockResolvedValue({
      success: true,
      data: [
        record({ last_used_at: '2026-02-01T10:00:00Z' }),
        record({ id: 'r'.repeat(24), label: 'Old', revoked_at: '2026-01-05T10:00:00Z' }),
      ],
    });

    await renderSection();

    const table = screen.getByRole('table', { name: 'Partner API keys' });
    expect(
      within(table)
        .getAllByRole('columnheader')
        .map((header) => header.textContent)
    ).toEqual(['Label', 'Created', 'Last used', 'Status', 'Actions']);
    const active = within(table).getByRole('row', { name: /Feed/ });
    const revoked = within(table).getByRole('row', { name: /Old/ });
    expect(within(active).getByText('Active')).toBeInTheDocument();
    expect(within(active).getByRole('button', { name: 'Revoke key Feed' })).toBeInTheDocument();
    expect(within(revoked).getByText('Revoked')).toBeInTheDocument();
    expect(within(revoked).queryByRole('button')).not.toBeInTheDocument();
    expect(within(revoked).queryByText('Active')).not.toBeInTheDocument();
    expect(within(active).queryByText('Never')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Create key' })).toBeInTheDocument();
  });

  it('shows Never for a key that was not used and wraps a long label', async () => {
    mocks.fetchAdminPartnerKeys.mockResolvedValue({
      success: true,
      data: [record({ label: 'acme_production_inventory_sync_service_eu_west_1_primary' })],
    });

    await renderSection();

    const row = screen.getByRole('row', { name: /acme_production/ });
    expect(within(row).getByText('Never')).toBeInTheDocument();
    expect(row.querySelector('[data-label="Label"]')?.className).toContain(
      'overflow-wrap:anywhere'
    );
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

  it('says plainly that this admin has no permission, with no error and no Create key button', async () => {
    mocks.fetchAdminPartnerKeys.mockResolvedValue({
      success: false,
      error: 'SECRET backend text',
      status: 403,
    });

    await renderSection();

    expect(screen.getByText(NO_PERMISSION_NOTICE)).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Create key' })).not.toBeInTheDocument();
    expect(document.body.innerHTML).not.toContain('SECRET');
  });

  it('shows a curated message with a Retry link when the keys cannot be read', async () => {
    mocks.fetchAdminPartnerKeys.mockResolvedValue({
      success: false,
      error: 'SECRET backend text',
      status: 503,
    });

    await renderSection();

    expect(screen.getByRole('alert')).toHaveTextContent('Something went wrong');
    expect(screen.getByRole('link', { name: 'Retry' })).toHaveAttribute(
      'href',
      '/admin/merchants/m1'
    );
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
