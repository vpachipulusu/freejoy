/** the desktop sidebar landmarks and current-page state. */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';

const mocks = vi.hoisted(() => ({ pathname: '/dashboard' }));
vi.mock('next/navigation', () => ({ usePathname: () => mocks.pathname }));
vi.mock('@/config/site', () => ({ siteConfig: { name: 'Acme Shop' } }));

import { Sidebar } from '@/components/dashboard/sidebar';
import { NAV_ITEMS } from '@/components/dashboard/nav-items';

describe('Sidebar', () => {
  beforeEach(() => {
    mocks.pathname = '/dashboard';
  });

  it('is an aside named Workspace holding a nav named Main', () => {
    render(<Sidebar />);
    const aside = screen.getByRole('complementary', { name: 'Workspace' });
    expect(within(aside).getByRole('navigation', { name: 'Main' })).toBeInTheDocument();
  });

  it('brand link shows the configured brand (siteConfig) and returns to the dashboard home', () => {
    render(<Sidebar />);
    expect(screen.getByRole('link', { name: 'Acme Shop' })).toHaveAttribute('href', '/dashboard');
    expect(screen.queryByText('Your App')).toBeNull();
  });

  it('lists every nav item as a link', () => {
    render(<Sidebar />);
    for (const item of NAV_ITEMS) {
      expect(screen.getByRole('link', { name: item.label })).toHaveAttribute('href', item.href);
    }
  });

  it('marks only the current page with aria-current', () => {
    mocks.pathname = '/dashboard/settings/billing';
    render(<Sidebar />);
    const current = screen
      .getAllByRole('link')
      .filter((link) => link.getAttribute('aria-current') === 'page');
    expect(current.map((link) => link.textContent)).toEqual(['Billing']);
  });

  it('marks nothing current on a page outside the nav', () => {
    mocks.pathname = '/dashboard/onboarding';
    render(<Sidebar />);
    expect(document.querySelector('[aria-current]')).toBeNull();
  });

  it('is hidden below 900px and uses the inverse panel and focus ring', () => {
    render(<Sidebar />);
    const aside = screen.getByRole('complementary', { name: 'Workspace' });
    expect(aside.className).toContain('max-[899px]:hidden');
    expect(aside.className).toContain('on-inverse');
    expect(aside.className).toContain('bg-feature');
  });

  it('gives every nav link a 44px target', () => {
    render(<Sidebar />);
    const nav = screen.getByRole('navigation', { name: 'Main' });
    for (const link of within(nav).getAllByRole('link')) {
      expect(link.className).toContain('min-h-11');
    }
  });

  it('links back to the public home page with the configured brand name', () => {
    render(<Sidebar />);
    const link = screen.getByRole('link', { name: 'Back to Acme Shop' });
    expect(link).toHaveAttribute('href', '/');
    expect(link.className).toContain('min-h-11');
    expect(link).not.toHaveAttribute('aria-current');
  });
});
