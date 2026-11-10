/**
 * The saved-offers list (FREEJ-28): a browser-local store of offer slugs. It is kept only in this browser:
 * there is no account and no database. The one place the slugs go is the Saved page, which sends them to the
 * `loadSavedOffersAction` server action to look the offers up (the action keeps nothing).
 *
 * Contract (documented in docs/developer/freejoy-frontend.md):
 * - One versioned key, `freejoy.saved.v1`, holding `{ "v": 1, "slugs": ["glow-cream", ...] }`: slugs
 *   only, oldest first. No titles, no ids of people, nothing else.
 * - Read defensively. Anything that is not that envelope (bad JSON, another version, too large) is
 *   treated as an empty list and never thrown; an invalid entry inside a good envelope is dropped.
 *   A read never rewrites the stored value; the next save replaces it.
 * - At most `MAX_SAVED_OFFERS` entries; saving past the cap drops the oldest.
 * - Storage can be missing or throw (private mode, blocked site data, quota). Reads then see an empty
 *   list and writes keep the list in memory for this page, so the heart still works until the tab closes.
 * - It is an external store for `useSyncExternalStore`: `subscribeSaved` / `getSavedSnapshot` /
 *   `getServerSavedSnapshot`. The snapshot is a frozen array that keeps its identity until the stored
 *   value changes. Changes made in another tab arrive through the `storage` event, and a page restored from the
 *   back/forward cache re-reads on `pageshow`.
 */
import { isValidSlug } from '@/lib/catalogue/slug';

export const SAVED_STORAGE_KEY = 'freejoy.saved.v1';
export const MAX_SAVED_OFFERS = 200;

const STORAGE_VERSION = 1;
/** 200 slugs of at most 100 characters is about 21 000 characters; anything bigger is not ours. */
const MAX_RAW_LENGTH = 32_768;

/** The envelope check, by hand: a schema library would ship to every catalogue page for four conditions. */
function slugsOf(json: unknown): readonly unknown[] | null {
  if (typeof json !== 'object' || json === null) return null;
  const { v, slugs } = json as { v?: unknown; slugs?: unknown };
  return v === STORAGE_VERSION && Array.isArray(slugs) ? slugs : null;
}

type SavedList = readonly string[];

const EMPTY: SavedList = Object.freeze([]) as SavedList;

type RawRead = { readable: true; raw: string | null } | { readable: false };

let cachedRaw: string | null | undefined;
let cachedList: SavedList = EMPTY;
/** Set while storage refuses writes: this page's list lives here until another tab writes successfully. */
let memoryList: SavedList | null = null;
const listeners = new Set<() => void>();

function freeze(slugs: readonly string[]): SavedList {
  return Object.freeze([...slugs]) as SavedList;
}

function newest(slugs: readonly string[]): readonly string[] {
  return slugs.length > MAX_SAVED_OFFERS ? slugs.slice(slugs.length - MAX_SAVED_OFFERS) : slugs;
}

function parseStored(raw: string | null): SavedList {
  if (raw === null || raw.length > MAX_RAW_LENGTH) return EMPTY;
  let json: unknown;
  try {
    json = JSON.parse(raw);
  } catch {
    return EMPTY;
  }
  const entries = slugsOf(json);
  if (entries === null) return EMPTY;
  const seen = new Set<string>();
  for (const entry of entries) {
    if (isValidSlug(entry)) seen.add(entry);
  }
  return freeze(newest([...seen]));
}

function readRaw(): RawRead {
  try {
    return { readable: true, raw: window.localStorage.getItem(SAVED_STORAGE_KEY) };
  } catch {
    return { readable: false };
  }
}

function notify(): void {
  listeners.forEach((listener) => listener());
}

/** The current list, oldest first. Same array until the stored value changes. */
export function getSavedSnapshot(): SavedList {
  if (memoryList !== null) return memoryList;
  const read = readRaw();
  if (!read.readable) return EMPTY;
  if (read.raw === cachedRaw) return cachedList;
  cachedRaw = read.raw;
  cachedList = parseStored(read.raw);
  return cachedList;
}

/** Nothing is saved as far as the server can know: server render and first client render agree. */
export function getServerSavedSnapshot(): SavedList {
  return EMPTY;
}

function commit(slugs: readonly string[]): void {
  const next = freeze(newest(slugs));
  const raw = JSON.stringify({ v: STORAGE_VERSION, slugs: next });
  try {
    window.localStorage.setItem(SAVED_STORAGE_KEY, raw);
    // A write that lands in storage we cannot read back would vanish from the UI: keep it in memory too.
    memoryList = readRaw().readable ? null : next;
    cachedRaw = raw;
    cachedList = next;
  } catch {
    memoryList = next;
  }
  notify();
}

export function addSaved(slug: string): void {
  if (!isValidSlug(slug)) return;
  const current = getSavedSnapshot();
  if (current.includes(slug)) return;
  commit([...current, slug]);
}

export function removeSaved(slug: string): void {
  removeSavedMany([slug]);
}

/**
 * Drops every listed slug in one write (used to prune offers that no longer exist) and returns how many it
 * really removed: a slug already gone (the visitor removed it meanwhile) does not count.
 */
export function removeSavedMany(slugs: readonly string[]): number {
  const current = getSavedSnapshot();
  const drop = new Set(slugs);
  const kept = current.filter((slug) => !drop.has(slug));
  if (kept.length === current.length) return 0;
  commit(kept);
  return current.length - kept.length;
}

/** Saves the slug if it is not saved, removes it if it is. Returns whether it is saved now. */
export function toggleSaved(slug: string): boolean {
  if (getSavedSnapshot().includes(slug)) {
    removeSaved(slug);
    return false;
  }
  addSaved(slug);
  return getSavedSnapshot().includes(slug);
}

function onStorageEvent(event: StorageEvent): void {
  // `key` is null when another tab cleared all storage.
  if (event.key !== null && event.key !== SAVED_STORAGE_KEY) return;
  memoryList = null;
  notify();
}

/** A page restored from the back/forward cache missed every event while it was away: read again. */
function onPageShow(event: PageTransitionEvent): void {
  if (event.persisted) notify();
}

/** For `useSyncExternalStore`. Listens for other tabs only while somebody is subscribed. */
export function subscribeSaved(listener: () => void): () => void {
  if (listeners.size === 0) {
    window.addEventListener('storage', onStorageEvent);
    window.addEventListener('pageshow', onPageShow);
  }
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) {
      window.removeEventListener('storage', onStorageEvent);
      window.removeEventListener('pageshow', onPageShow);
    }
  };
}
