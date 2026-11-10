/**
 * Catalogue failures. Pure (no server imports) so components can test `instanceof` without pulling
 * the server-only API wrapper into their module graph.
 */

/** Shown to visitors whenever the catalogue cannot be read. Never carries backend text. */
export const CATALOGUE_UNAVAILABLE_MESSAGE =
  'We could not load the freebies just now. Please try again in a moment.';

/**
 * The catalogue API could not be read (unreachable, timed out, rate limited or a server error). Thrown
 * rather than returned so `unstable_cache` never stores it and a page never renders an empty list as if
 * it were the real answer.
 */
export class CatalogueUnavailableError extends Error {
  constructor() {
    super('Catalogue unavailable');
    this.name = 'CatalogueUnavailableError';
  }
}

/**
 * The catalogue API answered 429: the backend's per-address budget is spent. A kind of outage (so every page
 * handles it already), but callers that fan out many reads (the Saved page) stop asking the moment they see it.
 */
export class CatalogueRateLimitedError extends CatalogueUnavailableError {
  constructor() {
    super();
    this.name = 'CatalogueRateLimitedError';
  }
}

export type Loaded<T> = { ok: true; data: T } | { ok: false };

/**
 * Awaits a catalogue read and folds an outage into `{ ok: false }` so a page can render its own error
 * state. Anything that is not a catalogue outage is a bug and keeps propagating.
 */
export async function settle<T>(read: Promise<T>): Promise<Loaded<T>> {
  try {
    return { ok: true, data: await read };
  } catch (error) {
    if (error instanceof CatalogueUnavailableError) return { ok: false };
    throw error;
  }
}
