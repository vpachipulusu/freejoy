import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

type Store = typeof import('@/lib/saved/saved-store');

const KEY = 'freejoy.saved.v1';

/** A fresh module per test: the store keeps a small in-memory cache that must not leak between cases. */
async function loadStore(): Promise<Store> {
  vi.resetModules();
  return import('@/lib/saved/saved-store');
}

function stored(): unknown {
  const raw = window.localStorage.getItem(KEY);
  return raw === null ? null : JSON.parse(raw);
}

function fireStorageEvent(key: string | null) {
  window.dispatchEvent(new StorageEvent('storage', { key }));
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('saved store: reading', () => {
  it('starts empty and uses the same empty array for the server snapshot', async () => {
    const store = await loadStore();

    expect(store.getSavedSnapshot()).toEqual([]);
    expect(store.getServerSavedSnapshot()).toEqual([]);
    expect(store.getServerSavedSnapshot()).toBe(store.getServerSavedSnapshot());
  });

  it('reads a saved list written earlier, oldest first', async () => {
    window.localStorage.setItem(KEY, JSON.stringify({ v: 1, slugs: ['a-one', 'b-two'] }));
    const store = await loadStore();

    expect(store.getSavedSnapshot()).toEqual(['a-one', 'b-two']);
  });

  it('returns the same array until the stored value changes, so React does not re-render', async () => {
    window.localStorage.setItem(KEY, JSON.stringify({ v: 1, slugs: ['a-one'] }));
    const store = await loadStore();

    expect(store.getSavedSnapshot()).toBe(store.getSavedSnapshot());
  });

  it.each([
    ['not json', 'not json {'],
    ['an array', '[]'],
    ['a string', '"a-one"'],
    ['a foreign version', JSON.stringify({ v: 2, slugs: ['a-one'] })],
    ['no version', JSON.stringify({ slugs: ['a-one'] })],
    ['slugs that are not a list', JSON.stringify({ v: 1, slugs: 'a-one' })],
    ['null', 'null'],
    ['a value over the size limit', JSON.stringify({ v: 1, slugs: ['a'.repeat(40_000)] })],
  ])('discards %s without throwing', async (_name, raw) => {
    window.localStorage.setItem(KEY, raw);
    const store = await loadStore();

    expect(() => store.getSavedSnapshot()).not.toThrow();
    expect(store.getSavedSnapshot()).toEqual([]);
  });

  it('keeps the valid slugs of a good envelope and drops anything else in it', async () => {
    const slugs = [
      'good-one',
      42,
      null,
      '',
      'Has Capitals',
      'bad/slug',
      'good-one',
      'good-two',
      {},
    ];
    window.localStorage.setItem(KEY, JSON.stringify({ v: 1, slugs }));
    const store = await loadStore();

    expect(store.getSavedSnapshot()).toEqual(['good-one', 'good-two']);
  });

  it('keeps only the newest entries of a stored list that is over the cap', async () => {
    const slugs = Array.from({ length: 260 }, (_, index) => `offer-${index}`);
    window.localStorage.setItem(KEY, JSON.stringify({ v: 1, slugs }));
    const store = await loadStore();

    const snapshot = store.getSavedSnapshot();
    expect(snapshot).toHaveLength(store.MAX_SAVED_OFFERS);
    expect(snapshot[snapshot.length - 1]).toBe('offer-259');
    expect(snapshot[0]).toBe('offer-60');
  });

  it('does not overwrite foreign data just by reading it', async () => {
    window.localStorage.setItem(KEY, 'foreign');
    const store = await loadStore();
    store.getSavedSnapshot();

    expect(window.localStorage.getItem(KEY)).toBe('foreign');
  });
});

describe('saved store: changing', () => {
  it('adds a slug, persists the versioned envelope and notifies subscribers', async () => {
    const store = await loadStore();
    const listener = vi.fn();
    store.subscribeSaved(listener);

    store.addSaved('glow-cream');

    expect(store.getSavedSnapshot()).toEqual(['glow-cream']);
    expect(stored()).toEqual({ v: 1, slugs: ['glow-cream'] });
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it('stores slugs only: no titles or other fields', async () => {
    const store = await loadStore();
    store.addSaved('glow-cream');

    expect(Object.keys(stored() as object).sort()).toEqual(['slugs', 'v']);
    expect(window.localStorage.getItem(KEY)).not.toMatch(/title|name|email/i);
  });

  it('ignores a slug that is already saved without notifying', async () => {
    const store = await loadStore();
    store.addSaved('glow-cream');
    const listener = vi.fn();
    store.subscribeSaved(listener);

    store.addSaved('glow-cream');

    expect(store.getSavedSnapshot()).toEqual(['glow-cream']);
    expect(listener).not.toHaveBeenCalled();
  });

  it.each(['', 'Has Capitals', 'a b', '../x', 'x'.repeat(101), '<script>'])(
    'refuses the invalid slug %j',
    async (slug) => {
      const store = await loadStore();
      store.addSaved(slug);

      expect(store.getSavedSnapshot()).toEqual([]);
      expect(window.localStorage.getItem(KEY)).toBeNull();
    }
  );

  it('removes a slug and ignores one that is not saved', async () => {
    const store = await loadStore();
    store.addSaved('one');
    store.addSaved('two');
    const listener = vi.fn();
    store.subscribeSaved(listener);

    store.removeSaved('one');
    store.removeSaved('never-saved');

    expect(store.getSavedSnapshot()).toEqual(['two']);
    expect(stored()).toEqual({ v: 1, slugs: ['two'] });
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it('toggles and reports whether the slug is now saved', async () => {
    const store = await loadStore();

    expect(store.toggleSaved('glow-cream')).toBe(true);
    expect(store.getSavedSnapshot()).toEqual(['glow-cream']);
    expect(store.toggleSaved('glow-cream')).toBe(false);
    expect(store.getSavedSnapshot()).toEqual([]);
  });

  it('refuses to toggle an invalid slug on', async () => {
    const store = await loadStore();

    expect(store.toggleSaved('Not A Slug')).toBe(false);
    expect(store.getSavedSnapshot()).toEqual([]);
  });

  it('removes several slugs in one write and notifies once', async () => {
    const store = await loadStore();
    ['a', 'b', 'c', 'd'].forEach((slug) => store.addSaved(slug));
    const listener = vi.fn();
    store.subscribeSaved(listener);

    store.removeSavedMany(['b', 'd', 'not-saved']);

    expect(store.getSavedSnapshot()).toEqual(['a', 'c']);
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it('does nothing when removeSavedMany matches nothing', async () => {
    const store = await loadStore();
    store.addSaved('a');
    const listener = vi.fn();
    store.subscribeSaved(listener);

    store.removeSavedMany(['x', 'y']);
    store.removeSavedMany([]);

    expect(listener).not.toHaveBeenCalled();
  });

  it('keeps the newest entries when the cap is reached, dropping the oldest', async () => {
    const store = await loadStore();
    const total = store.MAX_SAVED_OFFERS + 5;
    for (let index = 0; index < total; index += 1) store.addSaved(`offer-${index}`);

    const snapshot = store.getSavedSnapshot();
    expect(snapshot).toHaveLength(store.MAX_SAVED_OFFERS);
    expect(snapshot[0]).toBe('offer-5');
    expect(snapshot[snapshot.length - 1]).toBe(`offer-${total - 1}`);
    expect((stored() as { slugs: string[] }).slugs).toHaveLength(store.MAX_SAVED_OFFERS);
  });

  it('replaces corrupt stored data on the next save', async () => {
    window.localStorage.setItem(KEY, 'corrupt');
    const store = await loadStore();

    store.addSaved('glow-cream');

    expect(stored()).toEqual({ v: 1, slugs: ['glow-cream'] });
  });

  it('never hands out a mutable snapshot', async () => {
    const store = await loadStore();
    store.addSaved('a');

    expect(Object.isFrozen(store.getSavedSnapshot())).toBe(true);
  });
});

describe('saved store: subscriptions and other tabs', () => {
  it('re-reads and notifies when another tab changes the key', async () => {
    const store = await loadStore();
    expect(store.getSavedSnapshot()).toEqual([]);
    const listener = vi.fn();
    store.subscribeSaved(listener);

    window.localStorage.setItem(KEY, JSON.stringify({ v: 1, slugs: ['from-tab-two'] }));
    fireStorageEvent(KEY);

    expect(listener).toHaveBeenCalledTimes(1);
    expect(store.getSavedSnapshot()).toEqual(['from-tab-two']);
  });

  it('notifies when another tab clears all storage (key is null)', async () => {
    const store = await loadStore();
    store.addSaved('a');
    const listener = vi.fn();
    store.subscribeSaved(listener);

    window.localStorage.clear();
    fireStorageEvent(null);

    expect(listener).toHaveBeenCalledTimes(1);
    expect(store.getSavedSnapshot()).toEqual([]);
  });

  it('ignores storage events for other keys', async () => {
    const store = await loadStore();
    const listener = vi.fn();
    store.subscribeSaved(listener);

    fireStorageEvent('something-else');

    expect(listener).not.toHaveBeenCalled();
  });

  it('stops notifying after unsubscribe and removes its window listener with the last subscriber', async () => {
    const add = vi.spyOn(window, 'addEventListener');
    const remove = vi.spyOn(window, 'removeEventListener');
    const store = await loadStore();
    const first = vi.fn();
    const second = vi.fn();
    const stopFirst = store.subscribeSaved(first);
    const stopSecond = store.subscribeSaved(second);

    expect(add.mock.calls.filter(([type]) => type === 'storage')).toHaveLength(1);
    stopFirst();
    store.addSaved('a');
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);

    stopSecond();
    expect(remove.mock.calls.filter(([type]) => type === 'storage')).toHaveLength(1);
  });
});

describe('saved store: storage that is unavailable', () => {
  it('shows an empty list and keeps toggling in memory when reading throws', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('denied', 'SecurityError');
    });
    const store = await loadStore();

    expect(() => store.getSavedSnapshot()).not.toThrow();
    expect(store.getSavedSnapshot()).toEqual([]);
    expect(() => store.addSaved('glow-cream')).not.toThrow();
    expect(store.getSavedSnapshot()).toEqual(['glow-cream']);
    store.removeSaved('glow-cream');
    expect(store.getSavedSnapshot()).toEqual([]);
  });

  it('keeps the new list in memory when writing throws (quota or private mode) and still notifies', async () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('full', 'QuotaExceededError');
    });
    const store = await loadStore();
    const listener = vi.fn();
    store.subscribeSaved(listener);

    expect(() => store.addSaved('glow-cream')).not.toThrow();

    expect(store.getSavedSnapshot()).toEqual(['glow-cream']);
    expect(listener).toHaveBeenCalledTimes(1);
    expect(store.toggleSaved('glow-cream')).toBe(false);
    expect(store.getSavedSnapshot()).toEqual([]);
  });

  it('goes back to storage once another tab writes successfully', async () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('full', 'QuotaExceededError');
    });
    const store = await loadStore();
    store.subscribeSaved(vi.fn());
    store.addSaved('only-in-memory');
    setItem.mockRestore();

    window.localStorage.setItem(KEY, JSON.stringify({ v: 1, slugs: ['from-storage'] }));
    fireStorageEvent(KEY);

    expect(store.getSavedSnapshot()).toEqual(['from-storage']);
  });

  it('does not throw when window.localStorage itself throws on access', async () => {
    vi.spyOn(window, 'localStorage', 'get').mockImplementation(() => {
      throw new DOMException('denied', 'SecurityError');
    });
    const store = await loadStore();

    expect(store.getSavedSnapshot()).toEqual([]);
    expect(() => store.toggleSaved('glow-cream')).not.toThrow();
    expect(store.getSavedSnapshot()).toEqual(['glow-cream']);
  });
});
