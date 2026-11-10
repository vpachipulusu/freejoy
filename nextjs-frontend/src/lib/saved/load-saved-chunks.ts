import type { CardOffer } from '@/lib/catalogue/types';
import { SAVED_OFFERS_REQUEST_LIMIT } from '@/lib/saved/limits';

/** What the server action answers (`loadSavedOffersAction`), as far as this loop needs to know. */
export type SavedAnswer =
  | { success: true; data: { offers: CardOffer[]; missing: string[]; unavailable: string[] } }
  | { success: false; error: string };

export interface ChunksOutcome {
  offers: CardOffer[];
  /** The catalogue said these no longer exist: safe to remove from the list. */
  missing: string[];
  /** Everything else that was not shown: never remove these. */
  unavailable: string[];
  /** A call failed or was refused. Slugs of that chunk and later ones are in none of the lists above. */
  failed: boolean;
  /** `shouldContinue` said stop before a call. Slugs not yet asked about are in none of the lists. */
  cancelled: boolean;
}

interface Options {
  size?: number;
  /** Checked before every call; false stops the loop (the page is gone). */
  shouldContinue?: () => boolean;
}

function chunked<T>(items: readonly T[], size: number): T[][] {
  const chunks: T[][] = [];
  for (let start = 0; start < items.length; start += size)
    chunks.push(items.slice(start, start + size));
  return chunks;
}

async function ask(
  send: (chunk: string[]) => Promise<SavedAnswer>,
  chunk: string[]
): Promise<SavedAnswer> {
  try {
    return await send(chunk);
  } catch {
    // A network error or a refused call: the same curated failure, no detail kept.
    return { success: false, error: '' };
  }
}

/**
 * Asks the server about `slugs` a chunk at a time and gathers the answers. Pure of React so it can be tested
 * on its own. Rules, each from a way the loop could otherwise spin or lose a visitor's saves:
 *
 * - A slug the answer does not account for is `unavailable` (never silently dropped, so nothing waits forever),
 *   and an offer for a slug nobody asked about is ignored.
 * - A chunk that comes back with no offers and nothing missing means the catalogue cannot be read right now:
 *   every slug not yet asked about is called `unavailable` and the loop stops after that one call.
 * - A failed or thrown call stops the loop; what earlier chunks found is kept.
 * - It never throws.
 */
export async function loadSavedChunks(
  slugs: readonly string[],
  send: (chunk: string[]) => Promise<SavedAnswer>,
  { size = SAVED_OFFERS_REQUEST_LIMIT, shouldContinue = () => true }: Options = {}
): Promise<ChunksOutcome> {
  const outcome: ChunksOutcome = {
    offers: [],
    missing: [],
    unavailable: [],
    failed: false,
    cancelled: false,
  };
  const chunks = chunked(slugs, size);

  for (const [index, chunk] of chunks.entries()) {
    if (!shouldContinue()) return { ...outcome, cancelled: true };
    const answer = await ask(send, chunk);
    if (!answer.success) return { ...outcome, failed: true };

    const asked = new Set(chunk);
    const offers = answer.data.offers.filter((offer) => asked.has(offer.slug));
    const missing = answer.data.missing.filter((slug) => asked.has(slug));
    const accounted = new Set([...offers.map((offer) => offer.slug), ...missing]);
    const unavailable = chunk.filter((slug) => !accounted.has(slug));

    outcome.offers.push(...offers);
    outcome.missing.push(...missing);
    outcome.unavailable.push(...unavailable);

    if (offers.length === 0 && missing.length === 0) {
      outcome.unavailable.push(...chunks.slice(index + 1).flat());
      break;
    }
  }
  return outcome;
}
