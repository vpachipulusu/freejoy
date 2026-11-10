import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { SiteHeader } from '@/components/site/site-header';
import { SITE_TAB_ITEMS, activeTabHref } from '@/components/site/nav-items';

const fetchMock = vi.fn();

function answerWith(response: Response | Error) {
  if (response instanceof Error) fetchMock.mockRejectedValue(response);
  else fetchMock.mockResolvedValue(response);
}

describe('SiteHeader', () => {
  beforeEach(() => {
    answerWith(new Response(JSON.stringify({ signedIn: false }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it('renders the wordmark, the main nav, a search form and the alerts call to action', () => {
    render(<SiteHeader />);

    expect(screen.getByRole('link', { name: 'Freejoy home' })).toHaveAttribute('href', '/');
    expect(screen.getByRole('link', { name: 'All freebies' })).toHaveAttribute('href', '/freebies');
    expect(screen.getByRole('search')).toHaveAttribute('action', '/freebies');
    expect(screen.getAllByRole('link', { name: 'Get free alerts' })[0]).toHaveAttribute(
      'href',
      '/alerts'
    );
    expect(screen.getAllByRole('link', { name: 'Log in' })[0]).toHaveAttribute('href', '/login');
  });

  it('opens and closes the phone menu sheet', async () => {
    const user = userEvent.setup();
    render(<SiteHeader />);

    await user.click(screen.getByRole('button', { name: 'Open menu' }));
    expect(screen.getByRole('dialog', { name: 'Menu' })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Close menu' }));
    expect(screen.queryByRole('dialog', { name: 'Menu' })).not.toBeInTheDocument();
  });

  it('toggles the phone search form', async () => {
    const user = userEvent.setup();
    render(<SiteHeader />);

    await user.click(screen.getByRole('button', { name: 'Search freebies' }));
    expect(screen.getAllByRole('search')).toHaveLength(2);

    await user.click(screen.getByRole('button', { name: 'Close search' }));
    expect(screen.getAllByRole('search')).toHaveLength(1);
  });
});

describe('SiteHeader sign-in state', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it('shows Log in on the first render, before the status answer arrives', () => {
    fetchMock.mockReturnValue(new Promise(() => {}));
    render(<SiteHeader />);

    expect(screen.getAllByRole('link', { name: 'Log in' })).toHaveLength(1);
    expect(screen.queryByRole('link', { name: 'Dashboard' })).not.toBeInTheDocument();
  });

  it('shows Log in when the visitor is signed out', async () => {
    answerWith(new Response(JSON.stringify({ signedIn: false }), { status: 200 }));
    render(<SiteHeader />);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(screen.getByRole('link', { name: 'Log in' })).toHaveAttribute('href', '/login');
    expect(screen.queryByRole('link', { name: 'Dashboard' })).not.toBeInTheDocument();
  });

  it('shows Dashboard instead of Log in when signed in, and keeps the alerts button', async () => {
    answerWith(new Response(JSON.stringify({ signedIn: true }), { status: 200 }));
    render(<SiteHeader />);

    expect(await screen.findByRole('link', { name: 'Dashboard' })).toHaveAttribute(
      'href',
      '/dashboard'
    );
    expect(screen.queryByRole('link', { name: 'Log in' })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Get free alerts' })).toHaveAttribute(
      'href',
      '/alerts'
    );
  });

  it('shows Dashboard in the phone menu sheet too when signed in', async () => {
    const user = userEvent.setup();
    answerWith(new Response(JSON.stringify({ signedIn: true }), { status: 200 }));
    render(<SiteHeader />);
    await screen.findByRole('link', { name: 'Dashboard' });

    await user.click(screen.getByRole('button', { name: 'Open menu' }));
    const dialog = screen.getByRole('dialog', { name: 'Menu' });
    expect(within(dialog).getByRole('link', { name: 'Dashboard' })).toHaveAttribute(
      'href',
      '/dashboard'
    );
    expect(within(dialog).queryByRole('link', { name: 'Log in' })).not.toBeInTheDocument();
  });

  it('keeps Log in when the status request fails', async () => {
    answerWith(new TypeError('offline'));
    render(<SiteHeader />);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.getByRole('link', { name: 'Log in' })).toHaveAttribute('href', '/login');
    expect(screen.queryByRole('link', { name: 'Dashboard' })).not.toBeInTheDocument();
  });

  it('keeps Log in when the route is rate limited', async () => {
    answerWith(new Response(JSON.stringify({ error: 'slow' }), { status: 429 }));
    render(<SiteHeader />);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.getByRole('link', { name: 'Log in' })).toBeInTheDocument();
  });
});

describe('activeTabHref', () => {
  it('matches the home tab only on the root path', () => {
    expect(activeTabHref('/')).toBe('/');
    expect(activeTabHref('/freebies/glow-boosting-face-cream-sample')).toBe('/freebies');
    expect(activeTabHref('/alerts')).toBe('/alerts');
    expect(activeTabHref('/about')).toBeNull();
    expect(activeTabHref(null)).toBeNull();
  });

  it('reports the tab for each tab path and for pages beneath it, whatever the other tabs are', () => {
    for (const item of SITE_TAB_ITEMS) {
      expect(activeTabHref(item.href)).toBe(item.href);
    }
    expect(activeTabHref('/alerts/confirm')).toBe('/alerts');
    expect(activeTabHref('/saved/anything')).toBe('/saved');
  });
});
