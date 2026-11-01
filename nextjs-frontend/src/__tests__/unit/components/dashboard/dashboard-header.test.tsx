/** FREEJ-41: the account menu shows an Admin link only for an account the backend profile marks as admin. */
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('@/app/actions/auth-actions', () => ({ logoutAction: vi.fn() }));

import { DashboardHeader } from '@/components/dashboard/dashboard-header';

const base = { email: 'ada@example.com', dbDisplayName: 'Ada', dbAvatarUrl: null };

async function openMenu(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button'));
  return screen.findAllByRole('menuitem');
}

describe('DashboardHeader account menu', () => {
  it('links to /admin for an admin', async () => {
    render(<DashboardHeader user={{ ...base, isAdmin: true }} />);
    await openMenu(userEvent.setup());
    expect(await screen.findByRole('menuitem', { name: 'Admin' })).toHaveAttribute(
      'href',
      '/admin'
    );
  });

  it.each([[{}], [{ isAdmin: false }]])('has no Admin link for %j', async (extra) => {
    render(<DashboardHeader user={{ ...base, ...extra }} />);
    const items = await openMenu(userEvent.setup());
    expect(items.map((item) => item.textContent)).toEqual([
      'Dashboard',
      'Profile',
      'Security',
      'Back to Freejoy',
      'Log out',
    ]);
    expect(screen.queryByRole('menuitem', { name: 'Admin' })).not.toBeInTheDocument();
  });

  it('lists the items in order for an admin, with the separator just before Log out', async () => {
    render(<DashboardHeader user={{ ...base, isAdmin: true }} />);
    const items = await openMenu(userEvent.setup());

    expect(items.map((item) => item.textContent)).toEqual([
      'Dashboard',
      'Profile',
      'Security',
      'Admin',
      'Back to Freejoy',
      'Log out',
    ]);
    const separators = screen.getAllByRole('separator');
    const lastSeparator = separators[separators.length - 1];
    const logOut = items[items.length - 1];
    const admin = items[items.length - 3];
    expect(
      lastSeparator.compareDocumentPosition(logOut) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy();
    expect(
      lastSeparator.compareDocumentPosition(admin) & Node.DOCUMENT_POSITION_PRECEDING
    ).toBeTruthy();
  });

  it('reaches the Admin item with the arrow keys', async () => {
    const user = userEvent.setup();
    render(<DashboardHeader user={{ ...base, isAdmin: true }} />);
    await openMenu(user);

    // Radix focuses the menu content when it opens; each ArrowDown moves to the next item, wrapping at the end.
    for (let presses = 0; presses < 5; presses += 1) {
      await user.keyboard('{ArrowDown}');
      if (document.activeElement?.textContent === 'Admin') break;
    }

    expect(document.activeElement).toHaveTextContent('Admin');
    expect(document.activeElement).toHaveAttribute('href', '/admin');
  });

  it('gives every menu item a 44px minimum height', async () => {
    render(<DashboardHeader user={{ ...base, isAdmin: true }} />);
    const items = await openMenu(userEvent.setup());

    expect(items).toHaveLength(6);
    for (const item of items) expect(item.className).toContain('min-h-11');
  });

  it('links back to the public home page', async () => {
    render(<DashboardHeader user={base} />);
    await openMenu(userEvent.setup());
    expect(await screen.findByRole('menuitem', { name: 'Back to Freejoy' })).toHaveAttribute(
      'href',
      '/'
    );
  });
});
