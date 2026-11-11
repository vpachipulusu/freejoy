/** the phone tab bar and its More sheet (focus trap and restore come from Radix Dialog). */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, within } from '@testing-library/react';
import { setupUser } from '@/__tests__/unit/helpers/user';

const mocks = vi.hoisted(() => ({ pathname: '/admin' }));
vi.mock('next/navigation', () => ({ usePathname: () => mocks.pathname }));

import { AdminTabBar } from '@/components/admin/admin-tab-bar';
import { ADMIN_MORE_ITEMS, ADMIN_TAB_ITEMS } from '@/components/admin/admin-nav-items';

/** jsdom has no matchMedia: a controllable (min-width: 900px) query. */
function stubWideQuery(initiallyWide = false) {
  let matches = initiallyWide;
  const listeners = new Set<() => void>();
  vi.stubGlobal('matchMedia', () => ({
    get matches() {
      return matches;
    },
    addEventListener: (_: string, fn: () => void) => listeners.add(fn),
    removeEventListener: (_: string, fn: () => void) => listeners.delete(fn),
  }));
  return (wide: boolean) => {
    matches = wide;
    act(() => listeners.forEach((fn) => fn()));
  };
}

describe('AdminTabBar', () => {
  let setWide: (wide: boolean) => void;

  beforeEach(() => {
    mocks.pathname = '/admin';
    setWide = stubWideQuery();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('is a nav named Main with the primary items and a More button', () => {
    render(<AdminTabBar />);
    const nav = screen.getByRole('navigation', { name: 'Main' });
    for (const item of ADMIN_TAB_ITEMS) {
      expect(within(nav).getByRole('link', { name: item.label })).toHaveAttribute(
        'href',
        item.href
      );
    }
    expect(within(nav).getByRole('button', { name: 'More' })).toBeInTheDocument();
  });

  it('is hidden at 900px and up and is 56px high', () => {
    render(<AdminTabBar />);
    const nav = screen.getByRole('navigation', { name: 'Main' });
    expect(nav.className).toContain('min-[900px]:hidden');
    expect(nav.className).toContain('h-14');
  });

  it('marks the current tab with aria-current', () => {
    mocks.pathname = '/admin/offers/abc';
    render(<AdminTabBar />);
    const current = screen
      .getAllByRole('link')
      .filter((link) => link.getAttribute('aria-current') === 'page');
    expect(current.map((link) => link.textContent)).toEqual(['Freebies']);
  });

  it('opens a dialog listing the remaining items and returns focus to More on Escape', async () => {
    const user = setupUser();
    render(<AdminTabBar />);
    const more = screen.getByRole('button', { name: 'More' });
    expect(more).toHaveAttribute('aria-haspopup', 'dialog');

    await user.click(more);
    const dialog = await screen.findByRole('dialog', { name: 'More' });
    for (const item of ADMIN_MORE_ITEMS) {
      expect(within(dialog).getByRole('link', { name: item.label })).toHaveAttribute(
        'href',
        item.href
      );
    }
    expect(dialog.contains(document.activeElement)).toBe(true);

    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(more).toHaveFocus();
  });

  it('keeps Tab inside the open sheet', async () => {
    const user = setupUser();
    render(<AdminTabBar />);
    await user.click(screen.getByRole('button', { name: 'More' }));
    const dialog = await screen.findByRole('dialog', { name: 'More' });
    for (let i = 0; i < ADMIN_MORE_ITEMS.length + 3; i += 1) {
      await user.tab();
      expect(dialog.contains(document.activeElement)).toBe(true);
    }
  });

  it('closes the sheet when a link in it is chosen', async () => {
    const user = setupUser();
    render(<AdminTabBar />);
    await user.click(screen.getByRole('button', { name: 'More' }));
    const dialog = await screen.findByRole('dialog', { name: 'More' });
    await user.click(within(dialog).getByRole('link', { name: ADMIN_MORE_ITEMS[0].label }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('flags More as current, and the item inside the sheet, when the page is one of its items', async () => {
    mocks.pathname = ADMIN_MORE_ITEMS[0].href;
    const user = setupUser();
    render(<AdminTabBar />);
    const more = screen.getByRole('button', { name: /^More.*current section$/ });
    expect(more).toHaveAttribute('data-current', 'true');
    await user.click(more);
    const dialog = await screen.findByRole('dialog', { name: 'More' });
    expect(within(dialog).getByRole('link', { name: ADMIN_MORE_ITEMS[0].label })).toHaveAttribute(
      'aria-current',
      'page'
    );
  });

  it('closes the sheet when the page changes underneath it', async () => {
    const user = setupUser();
    const { rerender } = render(<AdminTabBar />);
    await user.click(screen.getByRole('button', { name: 'More' }));
    await screen.findByRole('dialog', { name: 'More' });

    mocks.pathname = '/admin/users';
    rerender(<AdminTabBar />);

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('closes the sheet when the viewport grows to 900px', async () => {
    const user = setupUser();
    render(<AdminTabBar />);
    await user.click(screen.getByRole('button', { name: 'More' }));
    await screen.findByRole('dialog', { name: 'More' });

    setWide(true);

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('does not bring the sheet back when returning to the page it was opened on', async () => {
    const user = setupUser();
    const { rerender } = render(<AdminTabBar />);
    await user.click(screen.getByRole('button', { name: 'More' }));
    await screen.findByRole('dialog', { name: 'More' });

    mocks.pathname = '/admin/users';
    rerender(<AdminTabBar />);
    mocks.pathname = '/admin';
    rerender(<AdminTabBar />);

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('does not bring the sheet back after a resize round trip', async () => {
    const user = setupUser();
    render(<AdminTabBar />);
    await user.click(screen.getByRole('button', { name: 'More' }));
    await screen.findByRole('dialog', { name: 'More' });

    setWide(true);
    setWide(false);

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('says More holds the current section only when it does', () => {
    render(<AdminTabBar />);
    expect(screen.getByRole('button', { name: 'More' })).not.toHaveAttribute('data-current');
  });
});
