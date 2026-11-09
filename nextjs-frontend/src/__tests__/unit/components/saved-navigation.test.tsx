import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { MobileTabBar } from '@/components/site/mobile-tab-bar';
import { SiteHeader } from '@/components/site/site-header';
import { SITE_TAB_ITEMS, activeTabHref } from '@/components/site/nav-items';

const nav = vi.hoisted(() => ({ pathname: '/' as string | null }));
vi.mock('next/navigation', () => ({
  usePathname: () => nav.pathname,
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn(), back: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

beforeEach(() => {
  nav.pathname = '/';
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(new Response(JSON.stringify({ signedIn: false }), { status: 200 }))
  );
});

describe('the phone tab bar', () => {
  it('has the four tabs of the design: Home, Browse, Saved, Alerts', () => {
    expect(SITE_TAB_ITEMS.map((item) => item.label)).toEqual(['Home', 'Browse', 'Saved', 'Alerts']);
    expect(SITE_TAB_ITEMS.find((item) => item.label === 'Saved')?.href).toBe('/saved');
  });

  it('links Saved to /saved from inside the Quick links navigation', () => {
    render(<MobileTabBar />);

    const bar = screen.getByRole('navigation', { name: 'Quick links' });
    expect(within(bar).getByRole('link', { name: 'Saved' })).toHaveAttribute('href', '/saved');
    expect(within(bar).getAllByRole('link')).toHaveLength(4);
  });

  it('marks Saved as the current page on /saved, and only Saved', () => {
    nav.pathname = '/saved';
    render(<MobileTabBar />);

    expect(screen.getByRole('link', { name: 'Saved' })).toHaveAttribute('aria-current', 'page');
    for (const name of ['Home', 'Browse', 'Alerts']) {
      expect(screen.getByRole('link', { name })).not.toHaveAttribute('aria-current');
    }
  });

  it('does not mark Saved as current elsewhere', () => {
    nav.pathname = '/freebies';
    render(<MobileTabBar />);

    expect(screen.getByRole('link', { name: 'Saved' })).not.toHaveAttribute('aria-current');
    expect(screen.getByRole('link', { name: 'Browse' })).toHaveAttribute('aria-current', 'page');
  });

  it('gives Saved a heart icon that screen readers skip', () => {
    render(<MobileTabBar />);

    const icon = screen.getByRole('link', { name: 'Saved' }).querySelector('svg');
    expect(icon).toHaveClass('lucide-heart');
    expect(icon).toHaveAttribute('aria-hidden', 'true');
  });

  it('shows no count badge', () => {
    render(<MobileTabBar />);

    expect(screen.getByRole('link', { name: 'Saved' }).textContent).toBe('Saved');
  });
});

describe('activeTabHref for the Saved page', () => {
  it('matches /saved and nothing else', () => {
    expect(activeTabHref('/saved')).toBe('/saved');
    expect(activeTabHref('/savedx')).toBeNull();
  });
});

describe('the desktop header entry point', () => {
  it('offers a Saved link to /saved next to Log in', () => {
    render(<SiteHeader />);

    const links = screen.getAllByRole('link', { name: 'Saved' });
    expect(links.length).toBeGreaterThan(0);
    expect(links[0]).toHaveAttribute('href', '/saved');
    expect(links[0].querySelector('svg')).toHaveAttribute('aria-hidden', 'true');
    expect(links[0].className).toContain('min-h-11');
  });

  it('marks it as the current page on /saved', () => {
    nav.pathname = '/saved';
    render(<SiteHeader />);

    expect(screen.getAllByRole('link', { name: 'Saved' })[0]).toHaveAttribute(
      'aria-current',
      'page'
    );
  });

  it('does not mark it as current on other pages', () => {
    render(<SiteHeader />);

    expect(screen.getAllByRole('link', { name: 'Saved' })[0]).not.toHaveAttribute('aria-current');
  });
});
