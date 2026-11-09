import { renderToString } from 'react-dom/server';
import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const mocks = vi.hoisted(() => ({ load: vi.fn() }));
vi.mock('@/app/actions/saved-actions', () => ({ loadSavedOffersAction: mocks.load }));

import { SavedOffers } from '@/components/saved/saved-offers';
import { FIXTURE_TODAY, MERCHANTS, OFFERS } from '@/lib/catalogue/fixtures';
import type { Offer } from '@/lib/catalogue/types';
import { SAVED_OFFERS_REQUEST_LIMIT } from '@/lib/saved/limits';
import { SAVED_STORAGE_KEY } from '@/lib/saved/saved-store';

const live = OFFERS.filter(
  (offer) => offer.status === 'published' && (!offer.endAt || offer.endAt >= FIXTURE_TODAY)
);
const [first, second, third] = live;

function withMerchant(offer: Offer): Offer {
  return { ...offer, merchant: MERCHANTS.find((item) => item.slug === offer.merchantSlug) };
}

/** What the server action answers: live fixture offers are found, everything else is missing. */
function answer(slugs: string[]) {
  const offers = slugs.flatMap((slug) => {
    const offer = live.find((item) => item.slug === slug);
    return offer ? [withMerchant(offer)] : [];
  });
  const found = new Set(offers.map((offer) => offer.slug));
  return {
    success: true as const,
    data: {
      offers,
      missing: slugs.filter((slug) => !found.has(slug)),
      unavailable: [] as string[],
    },
  };
}

function save(...slugs: string[]) {
  window.localStorage.setItem(SAVED_STORAGE_KEY, JSON.stringify({ v: 1, slugs }));
}

function storedSlugs(): string[] {
  const raw = window.localStorage.getItem(SAVED_STORAGE_KEY);
  return raw === null ? [] : (JSON.parse(raw) as { slugs: string[] }).slugs;
}

/** The polite live region: the count and any "removed" notice. Present from the first render so changes are announced. */
const summary = () => document.querySelector<HTMLElement>('[aria-live="polite"]')!;
const cardTitles = () => screen.getAllByRole('heading', { level: 3 }).map((h) => h.textContent);

beforeEach(() => {
  window.localStorage.clear();
  mocks.load.mockReset();
  mocks.load.mockImplementation(async (slugs: string[]) => answer(slugs));
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('SavedOffers: before and without anything saved', () => {
  it('server-renders a loading state, never the empty state, so a saved list does not flash "nothing"', () => {
    const html = renderToString(<SavedOffers />);

    expect(html).toContain('Loading your saved freebies');
    expect(html).not.toContain('Nothing saved yet');
  });

  it('shows a helpful empty state with a way to the catalogue, and asks the server for nothing', async () => {
    render(<SavedOffers />);

    expect(await screen.findByRole('heading', { name: 'Nothing saved yet' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Browse freebies' })).toHaveAttribute(
      'href',
      '/freebies'
    );
    expect(mocks.load).not.toHaveBeenCalled();
  });

  it('shows the empty state, without throwing, when the browser will not let the page read storage', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('denied', 'SecurityError');
    });

    render(<SavedOffers />);

    expect(await screen.findByRole('heading', { name: 'Nothing saved yet' })).toBeInTheDocument();
  });

  it('treats corrupt stored data as an empty list', async () => {
    window.localStorage.setItem(SAVED_STORAGE_KEY, '{"v":9,"slugs":["x"]}');

    render(<SavedOffers />);

    expect(await screen.findByRole('heading', { name: 'Nothing saved yet' })).toBeInTheDocument();
    expect(mocks.load).not.toHaveBeenCalled();
  });
});

describe('SavedOffers: the list', () => {
  it('lists saved offers as cards, newest first, with their hearts pressed', async () => {
    save(first.slug, second.slug);

    render(<SavedOffers />);

    await waitFor(() => expect(cardTitles()).toEqual([second.title, first.title]));
    expect(screen.getByRole('link', { name: second.title })).toHaveAttribute(
      'href',
      `/freebies/${second.slug}`
    );
    expect(
      screen.getByRole('button', { name: `Remove ${first.title} from saved` })
    ).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByText(merchantName(first))).toBeInTheDocument();
  });

  it('says how many are saved, in a polite live region', async () => {
    save(first.slug, second.slug);

    render(<SavedOffers />);

    await waitFor(() => expect(summary()).toHaveTextContent('2 saved freebies'));
    expect(summary()).not.toHaveAttribute('role', 'alert');
  });

  it('says "1 saved freebie" for one', async () => {
    save(first.slug);

    render(<SavedOffers />);

    expect(await screen.findByText('1 saved freebie')).toBeInTheDocument();
  });

  it('asks the server action for the saved slugs only, and never calls the network itself', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    save(first.slug, second.slug);

    render(<SavedOffers />);

    await waitFor(() => expect(mocks.load).toHaveBeenCalledTimes(1));
    expect([...mocks.load.mock.calls[0][0]].sort()).toEqual([first.slug, second.slug].sort());
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('shows a loading state while the offers are on their way', async () => {
    mocks.load.mockReturnValue(new Promise(() => {}));
    save(first.slug);

    render(<SavedOffers />);

    const loading = await screen.findByRole('status', { name: 'Loading your saved freebies' });
    expect(loading).toHaveAttribute('aria-busy', 'true');
  });

  it('removes a card when its heart is pressed, updates storage and keeps the rest', async () => {
    const user = userEvent.setup();
    save(first.slug, second.slug);
    render(<SavedOffers />);
    await screen.findByRole('heading', { level: 3, name: first.title });

    await user.click(screen.getByRole('button', { name: `Remove ${first.title} from saved` }));

    expect(screen.queryByRole('heading', { level: 3, name: first.title })).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 3, name: second.title })).toBeInTheDocument();
    expect(storedSlugs()).toEqual([second.slug]);
    expect(await screen.findByText('1 saved freebie')).toBeInTheDocument();
    expect(mocks.load).toHaveBeenCalledTimes(1);
  });

  it('moves focus to the count when the focused card disappears, so keyboard users are not dropped', async () => {
    const user = userEvent.setup();
    save(first.slug, second.slug);
    render(<SavedOffers />);
    await screen.findByRole('heading', { level: 3, name: first.title });

    screen.getByRole('button', { name: `Remove ${first.title} from saved` }).focus();
    await user.keyboard('{Enter}');

    await waitFor(() => expect(summary()).toHaveFocus());
  });

  it('shows the empty state when the last card is removed', async () => {
    const user = userEvent.setup();
    save(first.slug);
    render(<SavedOffers />);

    await user.click(await screen.findByRole('button', { name: /^Remove/ }));

    expect(await screen.findByRole('heading', { name: 'Nothing saved yet' })).toBeInTheDocument();
  });

  it('picks up an offer saved in another tab and asks only for the new one', async () => {
    save(first.slug);
    render(<SavedOffers />);
    await screen.findByRole('heading', { level: 3, name: first.title });
    mocks.load.mockClear();

    act(() => {
      save(first.slug, third.slug);
      window.dispatchEvent(new StorageEvent('storage', { key: SAVED_STORAGE_KEY }));
    });

    expect(await screen.findByRole('heading', { level: 3, name: third.title })).toBeInTheDocument();
    expect(mocks.load).toHaveBeenCalledTimes(1);
    expect(mocks.load).toHaveBeenCalledWith([third.slug]);
  });

  it('sends a long list in chunks no bigger than the request limit', async () => {
    const slugs = Array.from(
      { length: SAVED_OFFERS_REQUEST_LIMIT * 2 + 5 },
      (_, i) => `offer-${i}`
    );
    save(...slugs);

    render(<SavedOffers />);

    await waitFor(() => expect(mocks.load).toHaveBeenCalledTimes(3));
    const sizes = mocks.load.mock.calls.map(([chunk]) => (chunk as string[]).length);
    expect(Math.max(...sizes)).toBeLessThanOrEqual(SAVED_OFFERS_REQUEST_LIMIT);
    expect(sizes.reduce((sum, size) => sum + size, 0)).toBe(slugs.length);
  });
});

describe('SavedOffers: offers that are gone', () => {
  it('drops an offer that is no longer published, from the list and from storage, without an error', async () => {
    save('long-gone-offer', first.slug);

    render(<SavedOffers />);

    await screen.findByRole('heading', { level: 3, name: first.title });
    expect(cardTitles()).toEqual([first.title]);
    await waitFor(() => expect(storedSlugs()).toEqual([first.slug]));
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(summary()).toHaveTextContent(
      '1 saved freebie is no longer available and was removed from your list.'
    );
  });

  it('says so in the plural and falls back to the empty state when nothing is left', async () => {
    save('gone-one', 'gone-two');

    render(<SavedOffers />);

    expect(await screen.findByRole('heading', { name: 'Nothing saved yet' })).toBeInTheDocument();
    expect(summary()).toHaveTextContent(
      '2 saved freebies are no longer available and were removed from your list.'
    );
    expect(storedSlugs()).toEqual([]);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });
});

describe('SavedOffers: when the catalogue cannot be read', () => {
  it('keeps unreadable offers saved, says so, and tries again on request', async () => {
    const user = userEvent.setup();
    save(first.slug, second.slug);
    mocks.load.mockImplementationOnce(async () => ({
      success: true,
      data: { offers: [withMerchant(first)], missing: [], unavailable: [second.slug] },
    }));

    render(<SavedOffers />);

    await screen.findByRole('heading', { level: 3, name: first.title });
    expect(screen.getByText(/1 saved freebie could not be loaded just now/i)).toBeInTheDocument();
    expect(storedSlugs()).toEqual([first.slug, second.slug]);

    await user.click(screen.getByRole('button', { name: 'Try again' }));

    expect(
      await screen.findByRole('heading', { level: 3, name: second.title })
    ).toBeInTheDocument();
    expect(mocks.load).toHaveBeenLastCalledWith([second.slug]);
    expect(screen.queryByText(/could not be loaded/i)).not.toBeInTheDocument();
  });

  it('shows a curated error with a retry when the whole call fails, and removes nothing', async () => {
    const user = userEvent.setup();
    save(first.slug);
    mocks.load.mockResolvedValueOnce({ success: false, error: 'We could not load them.' });

    render(<SavedOffers />);

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('We could not load your saved freebies just now.');
    expect(storedSlugs()).toEqual([first.slug]);

    await user.click(within(alert).getByRole('button', { name: 'Try again' }));

    expect(await screen.findByRole('heading', { level: 3, name: first.title })).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('treats a rejected call the same way and leaves storage alone', async () => {
    save(first.slug);
    mocks.load.mockRejectedValueOnce(new Error('network down: secret detail'));

    render(<SavedOffers />);

    const alert = await screen.findByRole('alert');
    expect(alert).not.toHaveTextContent('secret detail');
    expect(storedSlugs()).toEqual([first.slug]);
  });

  it('does not keep retrying by itself', async () => {
    save(first.slug);
    mocks.load.mockResolvedValue({ success: false, error: 'x' });

    render(<SavedOffers />);
    await screen.findByRole('alert');
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 30));
    });

    expect(mocks.load).toHaveBeenCalledTimes(1);
  });
});

function merchantName(offer: Offer): string {
  return MERCHANTS.find((item) => item.slug === offer.merchantSlug)!.name;
}
