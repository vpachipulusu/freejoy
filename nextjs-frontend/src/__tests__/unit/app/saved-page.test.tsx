import { beforeEach, describe, expect, it, vi } from 'vitest';
import { renderToString } from 'react-dom/server';
import { render, screen, within } from '@testing-library/react';

const mocks = vi.hoisted(() => ({ cookies: vi.fn(), headers: vi.fn(), load: vi.fn() }));
vi.mock('next/headers', () => ({ cookies: mocks.cookies, headers: mocks.headers }));
vi.mock('@/app/actions/saved-actions', () => ({ loadSavedOffersAction: mocks.load }));

import robots from '@/app/robots';
import sitemap from '@/app/sitemap';
import SavedPage, { metadata } from '@/app/(public)/saved/page';
import { DISALLOWED_PATHS } from '@/lib/seo/robots';
import { buildSitemapEntries } from '@/lib/seo/sitemap';
import { CATEGORIES, FIXTURE_TODAY, OFFERS } from '@/lib/catalogue/fixtures';
import { SAVED_STORAGE_KEY } from '@/lib/saved/saved-store';
import { toCardOffer } from '@/lib/catalogue/types';

beforeEach(() => {
  window.localStorage.clear();
  mocks.load.mockReset();
  mocks.cookies.mockReset();
  mocks.headers.mockReset();
});

describe('the Saved page', () => {
  it('is titled, described and noindex (it is different on every device)', () => {
    expect(metadata.title).toBe('Saved freebies');
    expect(typeof metadata.description).toBe('string');
    expect(metadata.robots).toEqual({ index: false, follow: true });
  });

  it('has one h1 and tells people the list lives on this device only', () => {
    const { container } = render(<SavedPage />);

    expect(screen.getByRole('heading', { level: 1, name: 'Saved freebies' })).toBeInTheDocument();
    const note = screen.getByRole('note');
    expect(within(note).getByText('Saved on this device')).toBeInTheDocument();
    expect(note).toHaveTextContent(/clearing your browser data removes them/i);
    expect(note).toHaveTextContent(/not synced/i);
    expect(container.querySelectorAll('h1')).toHaveLength(1);
  });

  it('says plainly that it needs JavaScript when scripts are off', () => {
    // React leaves <noscript> empty on the client; the server markup is what a no-JS visitor gets.
    const html = renderToString(<SavedPage />);

    expect(html).toMatch(/<noscript>[^]*Saved freebies need JavaScript[^]*<\/noscript>/);
  });

  it('shows the empty state with a link to /freebies once the browser list is read', async () => {
    render(<SavedPage />);

    expect(await screen.findByRole('heading', { name: 'Nothing saved yet' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Browse freebies' })).toHaveAttribute(
      'href',
      '/freebies'
    );
  });

  it('lists what is saved, using the catalogue card', async () => {
    const offer = OFFERS.find((item) => item.status === 'published')!;
    window.localStorage.setItem(SAVED_STORAGE_KEY, JSON.stringify({ v: 1, slugs: [offer.slug] }));
    mocks.load.mockResolvedValue({
      success: true,
      data: { offers: [toCardOffer(offer)], missing: [], unavailable: [] },
    });

    render(<SavedPage />);

    const card = (await screen.findByRole('heading', { level: 3, name: offer.title })).closest(
      'article'
    );
    expect(card).toHaveClass('focus-proxy');
    expect(screen.getByRole('button', { name: `Save ${offer.title}`, pressed: true })).toBeInTheDocument();
  });

  it('runs the headings 1, 2, 3 without a gap once cards are listed', async () => {
    const offer = OFFERS.find((item) => item.status === 'published')!;
    window.localStorage.setItem(SAVED_STORAGE_KEY, JSON.stringify({ v: 1, slugs: [offer.slug] }));
    mocks.load.mockResolvedValue({
      success: true,
      data: { offers: [toCardOffer(offer)], missing: [], unavailable: [] },
    });
    const { container } = render(<SavedPage />);
    await screen.findByRole('heading', { level: 3, name: offer.title });

    const levels = [...container.querySelectorAll('h1, h2, h3')].map((heading) =>
      Number(heading.tagName.slice(1))
    );

    expect(levels).toEqual([1, 2, 3]);
  });

  it('reserves room for the list so the footer does not jump when it arrives', () => {
    const { container } = render(<SavedPage />);

    expect(container.querySelector('[class*="min-h-"]')).not.toBeNull();
  });

  it('never reads cookies or request headers, so nothing about the visitor reaches the server render', () => {
    render(<SavedPage />);

    expect(mocks.cookies).not.toHaveBeenCalled();
    expect(mocks.headers).not.toHaveBeenCalled();
  });
});

describe('the Saved page and search engines', () => {
  it('is not in the sitemap, whatever the catalogue source', () => {
    for (const source of ['api', 'fixture'] as const) {
      const entries = buildSitemapEntries({
        offers: OFFERS.map((offer) => ({ ...offer, source: 'api', demo: false })),
        categories: CATEGORIES,
        today: FIXTURE_TODAY,
        source,
      });
      expect(entries.map((entry) => entry.url).join('\n')).not.toContain('/saved');
    }
  });

  it('is not in the generated sitemap file', async () => {
    const entries = await sitemap();

    expect(entries.map((entry) => entry.url).join('\n')).not.toContain('/saved');
  });

  it('is left crawlable in robots.txt so the noindex can be seen', () => {
    expect(DISALLOWED_PATHS).not.toContain('/saved');
    const rules = [robots().rules].flat();
    expect(JSON.stringify(rules)).not.toContain('/saved');
  });
});
