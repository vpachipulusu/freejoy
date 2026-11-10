import { renderToString } from 'react-dom/server';
import { StrictMode, act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const mocks = vi.hoisted(() => ({ load: vi.fn() }));
vi.mock('@/app/actions/saved-actions', () => ({ loadSavedOffersAction: mocks.load }));

import { SavedOffers } from '@/components/saved/saved-offers';
import { FIXTURE_TODAY, MERCHANTS, OFFERS } from '@/lib/catalogue/fixtures';
import { toCardOffer, type CardOffer, type Offer } from '@/lib/catalogue/types';
import { SAVED_OFFERS_REQUEST_LIMIT } from '@/lib/saved/limits';
import { SAVED_STORAGE_KEY, removeSaved } from '@/lib/saved/saved-store';

// 48 cards with a user-event click each is slow on a busy machine; the default 5 s timed out once.
vi.setConfig({ testTimeout: 20_000 });

const live = OFFERS.filter(
  (offer) => offer.status === 'published' && (!offer.endAt || offer.endAt >= FIXTURE_TODAY)
);
const [first, second, third] = live;
const PAGE = SAVED_OFFERS_REQUEST_LIMIT;

function card(offer: Offer): CardOffer {
  return toCardOffer({ ...offer, merchant: MERCHANTS.find((m) => m.slug === offer.merchantSlug) });
}

/** A made-up live offer for any slug starting `offer-`, so a test can save hundreds. */
function synthetic(slug: string): CardOffer {
  return { ...card(first), id: `id-${slug}`, slug, title: `Synthetic ${slug}` };
}

/** What the server action answers: fixtures and `offer-N` slugs are found, everything else is missing. */
function answer(slugs: string[]) {
  const offers: CardOffer[] = [];
  const missing: string[] = [];
  for (const slug of slugs) {
    const fixture = live.find((item) => item.slug === slug);
    if (fixture) offers.push(card(fixture));
    else if (slug.startsWith('offer-')) offers.push(synthetic(slug));
    else missing.push(slug);
  }
  return { success: true as const, data: { offers, missing, unavailable: [] as string[] } };
}

const unavailableAnswer = (slugs: string[]) => ({
  success: true as const,
  data: { offers: [], missing: [], unavailable: slugs },
});

function save(...slugs: string[]) {
  window.localStorage.setItem(SAVED_STORAGE_KEY, JSON.stringify({ v: 1, slugs }));
}

function storedSlugs(): string[] {
  const raw = window.localStorage.getItem(SAVED_STORAGE_KEY);
  return raw === null ? [] : (JSON.parse(raw) as { slugs: string[] }).slugs;
}

function otherTabSaves(...slugs: string[]) {
  act(() => {
    save(...slugs);
    window.dispatchEvent(new StorageEvent('storage', { key: SAVED_STORAGE_KEY }));
  });
}

const offerSlugs = (count: number, from = 0) =>
  Array.from({ length: count }, (_, i) => `offer-${from + i}`);

/** The polite live region: the count and any "removed" notice. Present from the first render so changes are announced. */
const summary = () => document.querySelector<HTMLElement>('[aria-live="polite"]')!;
const cardTitles = () => screen.getAllByRole('heading', { level: 3 }).map((h) => h.textContent);
const heart = (offer: { title: string }) =>
  screen.getByRole('button', { name: `Save ${offer.title}`, pressed: true });
const allHearts = () => screen.getAllByRole('button', { pressed: true });
const wait = (ms = 30) =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, ms));
  });

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
    expect(heart(first)).toHaveAttribute('aria-pressed', 'true');
  });

  it('puts a visually hidden h2 above the cards so the headings run 1, 2, 3 without a gap', async () => {
    save(first.slug);

    render(<SavedOffers />);

    await screen.findByRole('heading', { level: 3, name: first.title });
    expect(screen.getByRole('heading', { level: 2, name: 'Your saved freebies' })).toHaveClass(
      'sr-only'
    );
  });

  it('says how many are saved, in a polite live region', async () => {
    save(first.slug, second.slug);

    render(<SavedOffers />);

    await waitFor(() => expect(summary()).toHaveTextContent('2 saved freebies'));
  });

  it('says "1 saved freebie" for one', async () => {
    save(first.slug);

    render(<SavedOffers />);

    expect(await screen.findByText('1 saved freebie')).toBeInTheDocument();
  });

  it('says "Loading your saved freebies" in the live region while it waits', async () => {
    mocks.load.mockReturnValue(new Promise(() => {}));
    save(first.slug);

    render(<SavedOffers />);

    await waitFor(() => expect(summary()).toHaveTextContent('Loading your saved freebies'));
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

  it('shows a skeleton that is plain hidden text for screen readers, not an empty busy status', async () => {
    mocks.load.mockReturnValue(new Promise(() => {}));
    save(first.slug);

    const { container } = render(<SavedOffers />);

    await waitFor(() => expect(container.querySelector('ul[aria-hidden="true"]')).not.toBeNull());
    expect(container.querySelector('[aria-busy]')).toBeNull();
    expect(container.querySelector('[role="status"]')).toBeNull();
    expect(container.querySelector('p.sr-only')).toHaveTextContent('Loading your saved freebies');
  });

  it('removes a card when its heart is pressed, updates storage and keeps the rest', async () => {
    const user = userEvent.setup();
    save(first.slug, second.slug);
    render(<SavedOffers />);
    await screen.findByRole('heading', { level: 3, name: first.title });

    await user.click(heart(first));

    expect(screen.queryByRole('heading', { level: 3, name: first.title })).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 3, name: second.title })).toBeInTheDocument();
    expect(storedSlugs()).toEqual([second.slug]);
    expect(await screen.findByText('1 saved freebie')).toBeInTheDocument();
    expect(mocks.load).toHaveBeenCalledTimes(1);
  });

  it('shows the empty state when the last card is removed', async () => {
    const user = userEvent.setup();
    save(first.slug);
    render(<SavedOffers />);

    await user.click(await screen.findByRole('button', { pressed: true }));

    expect(await screen.findByRole('heading', { name: 'Nothing saved yet' })).toBeInTheDocument();
  });

  it('picks up an offer saved in another tab and asks only for the new one', async () => {
    save(first.slug);
    render(<SavedOffers />);
    await screen.findByRole('heading', { level: 3, name: first.title });
    mocks.load.mockClear();

    otherTabSaves(first.slug, third.slug);

    expect(await screen.findByRole('heading', { level: 3, name: third.title })).toBeInTheDocument();
    expect(mocks.load).toHaveBeenCalledTimes(1);
    expect(mocks.load).toHaveBeenCalledWith([third.slug]);
  });
});

describe('SavedOffers: a long list is read one chunk at a time', () => {
  it('makes exactly one call for 200 saved slugs, then offers "Show more"', async () => {
    save(...offerSlugs(200));

    render(<SavedOffers />);

    await waitFor(() => expect(allHearts()).toHaveLength(PAGE));
    await wait();
    expect(mocks.load).toHaveBeenCalledTimes(1);
    expect(mocks.load.mock.calls[0][0]).toHaveLength(PAGE);
    // Newest saved first.
    expect(mocks.load.mock.calls[0][0]).toContain('offer-199');
    expect(summary()).toHaveTextContent(`${PAGE} of 200 saved freebies shown`);
    expect(screen.getByRole('button', { name: 'Show more' })).toBeInTheDocument();
  });

  it('asks for the next chunk only when "Show more" is pressed, and puts focus on its first card', async () => {
    const user = userEvent.setup();
    save(...offerSlugs(200));
    render(<SavedOffers />);
    await waitFor(() => expect(allHearts()).toHaveLength(PAGE));

    await user.click(screen.getByRole('button', { name: 'Show more' }));

    await waitFor(() => expect(allHearts()).toHaveLength(PAGE * 2));
    expect(mocks.load).toHaveBeenCalledTimes(2);
    const [firstChunk, secondChunk] = mocks.load.mock.calls.map(([slugs]) => slugs as string[]);
    expect(secondChunk.filter((slug) => firstChunk.includes(slug))).toEqual([]);
    expect(allHearts()[PAGE]).toHaveFocus();
  });

  it('keeps focus on the button while the next chunk is loading', async () => {
    const user = userEvent.setup();
    save(...offerSlugs(60));
    render(<SavedOffers />);
    await waitFor(() => expect(allHearts()).toHaveLength(PAGE));
    mocks.load.mockReturnValue(new Promise(() => {}));

    await user.click(screen.getByRole('button', { name: 'Show more' }));

    const button = await screen.findByRole('button', { name: /Loading/ });
    expect(button).toHaveAttribute('aria-disabled', 'true');
    expect(button).toHaveFocus();
  });

  it('hides "Show more" once everything is shown', async () => {
    const user = userEvent.setup();
    save(...offerSlugs(PAGE + 3));
    render(<SavedOffers />);
    await user.click(await screen.findByRole('button', { name: 'Show more' }));

    await waitFor(() => expect(allHearts()).toHaveLength(PAGE + 3));
    expect(screen.queryByRole('button', { name: 'Show more' })).not.toBeInTheDocument();
    expect(summary()).toHaveTextContent(`${PAGE + 3} saved freebies`);
  });

  it('stops after one call when the first chunk comes back all unavailable, and removes nothing', async () => {
    mocks.load.mockImplementation(async (slugs: string[]) => unavailableAnswer(slugs));
    save(...offerSlugs(101));

    render(<SavedOffers />);

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'could not load your saved freebies'
    );
    await wait();
    expect(mocks.load).toHaveBeenCalledTimes(1);
    expect(storedSlugs()).toHaveLength(101);
    expect(screen.queryByRole('button', { name: 'Show more' })).not.toBeInTheDocument();
  });

  it('keeps the first chunk when a later chunk fails, removes nothing, and does not keep asking', async () => {
    const user = userEvent.setup();
    save(...offerSlugs(60));
    render(<SavedOffers />);
    await waitFor(() => expect(allHearts()).toHaveLength(PAGE));
    mocks.load.mockResolvedValue({ success: false, error: 'busy' });

    await user.click(screen.getByRole('button', { name: 'Show more' }));

    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(allHearts()).toHaveLength(PAGE);
    await wait();
    expect(mocks.load).toHaveBeenCalledTimes(2);
    expect(storedSlugs()).toHaveLength(60);
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
    expect(mocks.load).toHaveBeenCalledTimes(1);
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
    expect(mocks.load).toHaveBeenCalledTimes(1);
  });

  it('does not announce a slug the visitor removed while the answer was on its way', async () => {
    let resolve!: (value: unknown) => void;
    mocks.load.mockReturnValue(new Promise((done) => (resolve = done)));
    save('gone-one', first.slug);
    render(<SavedOffers />);
    await waitFor(() => expect(mocks.load).toHaveBeenCalledTimes(1));

    act(() => removeSaved('gone-one'));
    await act(async () => {
      resolve({
        success: true,
        data: { offers: [card(first)], missing: ['gone-one'], unavailable: [] },
      });
    });

    await screen.findByRole('heading', { level: 3, name: first.title });
    expect(summary()).not.toHaveTextContent(/no longer available/);
  });

  it('keeps an offer saved in another tab while a prune is in flight', async () => {
    let resolve!: (value: unknown) => void;
    mocks.load.mockReturnValueOnce(new Promise((done) => (resolve = done)));
    save('gone-one', first.slug);
    render(<SavedOffers />);
    await waitFor(() => expect(mocks.load).toHaveBeenCalledTimes(1));

    otherTabSaves('gone-one', first.slug, 'offer-9');
    await act(async () => {
      resolve({
        success: true,
        data: { offers: [card(first)], missing: ['gone-one'], unavailable: [] },
      });
    });

    await waitFor(() => expect(storedSlugs()).toEqual([first.slug, 'offer-9']));
    expect(
      await screen.findByRole('heading', { level: 3, name: 'Synthetic offer-9' })
    ).toBeVisible();
  });
});

describe('SavedOffers: when the catalogue cannot be read', () => {
  it('keeps unreadable offers saved, counts honestly, and tries again on request', async () => {
    const user = userEvent.setup();
    save(first.slug, second.slug);
    mocks.load.mockImplementationOnce(async () => ({
      success: true,
      data: { offers: [card(first)], missing: [], unavailable: [second.slug] },
    }));

    render(<SavedOffers />);

    await screen.findByRole('heading', { level: 3, name: first.title });
    const note = screen.getByText(/1 saved freebie could not be loaded just now/i);
    expect(note.closest('[role="status"]')).not.toBeNull();
    expect(summary()).toHaveTextContent('1 of 2 saved freebies shown');
    expect(summary()).not.toHaveTextContent(/^1 saved freebie$/);
    expect(storedSlugs()).toEqual([first.slug, second.slug]);

    await user.click(screen.getByRole('button', { name: 'Try again' }));

    expect(
      await screen.findByRole('heading', { level: 3, name: second.title })
    ).toBeInTheDocument();
    expect(mocks.load).toHaveBeenLastCalledWith([second.slug]);
    expect(screen.queryByText(/could not be loaded/i)).not.toBeInTheDocument();
    await waitFor(() => expect(summary()).toHaveTextContent('2 saved freebies'));
  });

  it('shows an alert, not an empty grid, when every saved offer is unavailable, and keeps them all', async () => {
    mocks.load.mockImplementation(async (slugs: string[]) => unavailableAnswer(slugs));
    save(first.slug, second.slug);

    render(<SavedOffers />);

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('They are still saved on this device');
    expect(screen.queryByRole('heading', { level: 3 })).not.toBeInTheDocument();
    expect(storedSlugs()).toEqual([first.slug, second.slug]);
  });

  it('shows a curated error with a retry when the whole call fails, and removes nothing', async () => {
    const user = userEvent.setup();
    save(first.slug);
    mocks.load.mockResolvedValueOnce({ success: false, error: 'We could not load them.' });

    render(<SavedOffers />);

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('We could not load your saved freebies just now.');
    expect(storedSlugs()).toEqual([first.slug]);

    await user.click(screen.getByRole('button', { name: 'Try again' }));

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
    await wait();

    expect(mocks.load).toHaveBeenCalledTimes(1);
  });
});

describe('SavedOffers: answers it cannot trust', () => {
  it('calls a slug the answer did not mention unavailable, and does not loop', async () => {
    mocks.load.mockResolvedValue({
      success: true,
      data: { offers: [card(second)], missing: [], unavailable: [] },
    });
    save(first.slug);

    render(<SavedOffers />);

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'could not load your saved freebies'
    );
    await wait();
    expect(mocks.load).toHaveBeenCalledTimes(1);
    expect(storedSlugs()).toEqual([first.slug]);
    expect(screen.queryByRole('heading', { level: 3 })).not.toBeInTheDocument();
  });

  it('asks for a stored slug that is also an Object property name, and nothing throws', async () => {
    save('constructor', 'valueof');

    render(<SavedOffers />);

    await screen.findByRole('heading', { name: 'Nothing saved yet' });
    expect(mocks.load).toHaveBeenCalledTimes(1);
    expect([...mocks.load.mock.calls[0][0]].sort()).toEqual(['constructor', 'valueof']);
  });

  it('does not ask for the next chunk after the page is gone', async () => {
    let resolve!: (value: unknown) => void;
    mocks.load.mockReturnValueOnce(new Promise((done) => (resolve = done)));
    save(...offerSlugs(40));
    const { unmount } = render(<SavedOffers />);
    await waitFor(() => expect(mocks.load).toHaveBeenCalledTimes(1));

    unmount();
    await act(async () => {
      resolve({
        success: true,
        data: { offers: [], missing: offerSlugs(PAGE, 16), unavailable: [] },
      });
    });
    await wait();

    expect(mocks.load).toHaveBeenCalledTimes(1);
    expect(storedSlugs()).toHaveLength(40);
  });

  it('still loads everything under StrictMode, with a single call', async () => {
    save(first.slug, second.slug);

    render(
      <StrictMode>
        <SavedOffers />
      </StrictMode>
    );

    await waitFor(() => expect(cardTitles()).toEqual([second.title, first.title]));
    expect(mocks.load).toHaveBeenCalledTimes(1);
  });
});

describe('SavedOffers: focus', () => {
  async function renderThree() {
    save(first.slug, second.slug, third.slug);
    render(<SavedOffers />);
    // Newest first: third, second, first.
    await waitFor(() => expect(cardTitles()).toEqual([third.title, second.title, first.title]));
  }

  it('moves focus to the next card heart when the middle card is removed from the keyboard', async () => {
    const user = userEvent.setup();
    await renderThree();

    heart(second).focus();
    await user.keyboard('{Enter}');

    await waitFor(() => expect(heart(first)).toHaveFocus());
  });

  it('moves focus to the previous card heart when the last card is removed', async () => {
    const user = userEvent.setup();
    await renderThree();

    heart(first).focus();
    await user.keyboard('{Enter}');

    await waitFor(() => expect(heart(second)).toHaveFocus());
  });

  it('moves focus to the "Nothing saved yet" heading when the only card is removed', async () => {
    const user = userEvent.setup();
    save(first.slug);
    render(<SavedOffers />);
    await screen.findByRole('heading', { level: 3, name: first.title });

    heart(first).focus();
    await user.keyboard('{Enter}');

    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Nothing saved yet' })).toHaveFocus()
    );
  });

  it('does not move focus when the removal came from another tab', async () => {
    await renderThree();
    (document.activeElement as HTMLElement | null)?.blur();

    otherTabSaves(first.slug, third.slug);

    await waitFor(() => expect(cardTitles()).toEqual([third.title, first.title]));
    expect(document.body).toHaveFocus();
  });

  it('does not drop focus to the page when "Try again" is pressed in the error alert', async () => {
    const user = userEvent.setup();
    save(first.slug);
    mocks.load.mockResolvedValueOnce({ success: false, error: 'x' });
    render(<SavedOffers />);
    const retry = await screen.findByRole('button', { name: 'Try again' });

    retry.focus();
    await user.keyboard('{Enter}');

    await screen.findByRole('heading', { level: 3, name: first.title });
    expect(document.body).not.toHaveFocus();
  });

  it('does not drop focus to the page when "Try again" is pressed beside some cards', async () => {
    const user = userEvent.setup();
    save(first.slug, second.slug);
    mocks.load.mockImplementationOnce(async () => ({
      success: true,
      data: { offers: [card(first)], missing: [], unavailable: [second.slug] },
    }));
    render(<SavedOffers />);
    const retry = await screen.findByRole('button', { name: 'Try again' });

    retry.focus();
    await user.keyboard('{Enter}');

    await screen.findByRole('heading', { level: 3, name: second.title });
    expect(document.body).not.toHaveFocus();
  });
});
