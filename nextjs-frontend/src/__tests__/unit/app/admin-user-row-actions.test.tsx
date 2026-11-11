/**
 * The Users row menu. Every item does something observable: View Details and Edit User navigate, Suspend User and
 * Restore Access open a confirmation, call the server action and report the outcome.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { setupUser } from '@/__tests__/unit/helpers/user';

const mocks = vi.hoisted(() => ({
  banUserAction: vi.fn(),
  unbanUserAction: vi.fn(),
  toast: vi.fn(),
  refresh: vi.fn(),
}));

vi.mock('@/app/actions/admin/user-actions', () => ({
  banUserAction: mocks.banUserAction,
  unbanUserAction: mocks.unbanUserAction,
}));
vi.mock('@/hooks/use-toast', () => ({ toast: mocks.toast }));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ refresh: mocks.refresh, push: vi.fn() }),
}));

import { UserRowActions } from '@/app/(admin)/admin/users/user-row-actions';

const ok = { success: true, data: { message: 'done' } };

async function openMenu(
  user: ReturnType<typeof userEvent.setup>,
  name: string | RegExp = /open menu/i
) {
  await user.click(screen.getByRole('button', { name }));
  return screen.findByRole('menu');
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.banUserAction.mockResolvedValue(ok);
  mocks.unbanUserAction.mockResolvedValue(ok);
});

describe('UserRowActions menu', () => {
  it('links View Details and Edit User to their pages', async () => {
    const user = setupUser();
    render(<UserRowActions userId="user-123" userLabel="Ada" />);

    await openMenu(user, 'Open menu for Ada');

    expect(screen.getByRole('menuitem', { name: /view details/i })).toHaveAttribute(
      'href',
      '/admin/users/user-123'
    );
    expect(screen.getByRole('menuitem', { name: /edit user/i })).toHaveAttribute(
      'href',
      '/admin/users/user-123/edit'
    );
    expect(screen.getByRole('menuitem', { name: /suspend user/i })).toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: /restore access/i })).toBeInTheDocument();
  });

  it('encodes the user id in both links', async () => {
    const user = setupUser();
    render(<UserRowActions userId="a b" userLabel="Ada" />);

    await openMenu(user, 'Open menu for Ada');

    expect(screen.getByRole('menuitem', { name: /view details/i })).toHaveAttribute(
      'href',
      '/admin/users/a%20b'
    );
    expect(screen.getByRole('menuitem', { name: /edit user/i })).toHaveAttribute(
      'href',
      '/admin/users/a%20b/edit'
    );
  });

  it('gives every menu item a 44px touch target', async () => {
    const user = setupUser();
    render(<UserRowActions userId="user-123" userLabel="Ada" />);

    await openMenu(user, 'Open menu for Ada');

    const items = screen.getAllByRole('menuitem');
    expect(items).toHaveLength(4);
    for (const item of items) expect(item).toHaveClass('min-h-11');
  });

  it('names the trigger after the user', () => {
    render(<UserRowActions userId="user-123" userLabel="Ada" />);

    expect(screen.getByRole('button', { name: 'Open menu for Ada' })).toBeInTheDocument();
  });
});

describe('suspending a user', () => {
  async function openSuspend(user: ReturnType<typeof userEvent.setup>) {
    render(<UserRowActions userId="user-123" userLabel="Ada" />);
    await openMenu(user, 'Open menu for Ada');
    await user.click(screen.getByRole('menuitem', { name: /suspend user/i }));
    return screen.findByRole('dialog');
  }

  it('asks for confirmation and does not call the action until confirmed', async () => {
    const user = setupUser();
    const dialog = await openSuspend(user);

    expect(within(dialog).getByText('Suspend this user?')).toBeInTheDocument();
    expect(within(dialog).getByText('Ada')).toBeInTheDocument();
    expect(mocks.banUserAction).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));

    expect(mocks.banUserAction).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('calls the action with the optional reason, announces success and refreshes', async () => {
    const user = setupUser();
    const dialog = await openSuspend(user);

    await user.type(within(dialog).getByLabelText(/reason/i), '  spam  ');
    await user.click(within(dialog).getByRole('button', { name: 'Suspend user' }));

    await waitFor(() => expect(mocks.banUserAction).toHaveBeenCalledWith('user-123', 'spam'));
    expect(mocks.toast).toHaveBeenCalledWith({ title: 'Ada suspended' });
    expect(mocks.refresh).toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('sends no reason when the box is left empty', async () => {
    const user = setupUser();
    const dialog = await openSuspend(user);

    await user.click(within(dialog).getByRole('button', { name: 'Suspend user' }));

    await waitFor(() => expect(mocks.banUserAction).toHaveBeenCalledWith('user-123', undefined));
  });

  it('keeps the dialog open and shows the refusal when the action fails', async () => {
    mocks.banUserAction.mockResolvedValue({
      success: false,
      error: 'You do not have permission to do that',
    });
    const user = setupUser();
    const dialog = await openSuspend(user);

    await user.click(within(dialog).getByRole('button', { name: 'Suspend user' }));

    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      'You do not have permission to do that'
    );
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(mocks.toast).not.toHaveBeenCalled();
    expect(mocks.refresh).not.toHaveBeenCalled();
  });

  it('shows a generic message when the action call itself throws', async () => {
    mocks.banUserAction.mockRejectedValue(new Error('network'));
    const user = setupUser();
    const dialog = await openSuspend(user);

    await user.click(within(dialog).getByRole('button', { name: 'Suspend user' }));

    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      'Something went wrong. Please try again.'
    );
  });
});

describe('suspend dialog focus and in-flight behaviour', () => {
  async function openSuspend(user: ReturnType<typeof userEvent.setup>) {
    render(<UserRowActions userId="user-123" userLabel="Ada" />);
    const trigger = screen.getByRole('button', { name: 'Open menu for Ada' });
    await openMenu(user, 'Open menu for Ada');
    await user.click(screen.getByRole('menuitem', { name: /suspend user/i }));
    return { trigger, dialog: await screen.findByRole('dialog') };
  }

  it('returns focus to the row trigger and releases the page after Cancel', async () => {
    const user = setupUser();
    const { trigger, dialog } = await openSuspend(user);

    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    await waitFor(() => expect(trigger).toHaveFocus());
    expect(document.body.style.pointerEvents).toBe('');
  });

  it('returns focus to the row trigger and releases the page after a successful suspend', async () => {
    const user = setupUser();
    const { trigger, dialog } = await openSuspend(user);

    await user.click(within(dialog).getByRole('button', { name: 'Suspend user' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    await waitFor(() => expect(trigger).toHaveFocus());
    expect(document.body.style.pointerEvents).toBe('');
  });

  it('runs once while in flight: one call, a busy button, Cancel disabled, Escape ignored', async () => {
    mocks.banUserAction.mockReturnValue(new Promise(() => {}));
    const user = setupUser();
    const { dialog } = await openSuspend(user);

    const confirm = within(dialog).getByRole('button', { name: 'Suspend user' });
    await user.click(confirm);
    await user.click(within(dialog).getByRole('button', { name: 'Suspending…' }));

    expect(mocks.banUserAction).toHaveBeenCalledTimes(1);
    const busy = within(dialog).getByRole('button', { name: 'Suspending…' });
    expect(busy).toHaveAttribute('aria-disabled', 'true');
    expect(within(dialog).getByRole('button', { name: 'Cancel' })).toBeDisabled();

    await user.keyboard('{Escape}');

    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });
});

describe('restoring a user', () => {
  it('confirms, calls the unban action and announces success', async () => {
    const user = setupUser();
    render(<UserRowActions userId="user-123" userLabel="Ada" />);
    await openMenu(user, 'Open menu for Ada');
    await user.click(screen.getByRole('menuitem', { name: /restore access/i }));
    const dialog = await screen.findByRole('dialog');

    expect(within(dialog).getByText('Restore access for this user?')).toBeInTheDocument();
    expect(within(dialog).queryByLabelText(/reason/i)).not.toBeInTheDocument();
    expect(mocks.unbanUserAction).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole('button', { name: 'Restore access' }));

    await waitFor(() => expect(mocks.unbanUserAction).toHaveBeenCalledWith('user-123'));
    expect(mocks.banUserAction).not.toHaveBeenCalled();
    expect(mocks.toast).toHaveBeenCalledWith({ title: 'Ada restored' });
    expect(mocks.refresh).toHaveBeenCalled();
  });

  it('shows the refusal in the dialog when restoring fails', async () => {
    mocks.unbanUserAction.mockResolvedValue({ success: false, error: 'User not found' });
    const user = setupUser();
    render(<UserRowActions userId="user-123" userLabel="Ada" />);
    await openMenu(user, 'Open menu for Ada');
    await user.click(screen.getByRole('menuitem', { name: /restore access/i }));
    const dialog = await screen.findByRole('dialog');

    await user.click(within(dialog).getByRole('button', { name: 'Restore access' }));

    expect(await within(dialog).findByRole('alert')).toHaveTextContent('User not found');
  });
});
