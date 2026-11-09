'use server';

import { z } from 'zod';
import { logger } from '@/lib/logger';
import {
  CatalogueUnavailableError,
  getMerchantBySlug,
  getToday,
  isLive,
  lookupOffer,
  type Offer,
} from '@/lib/catalogue';
import { MAX_SLUG_LENGTH, SLUG_PATTERN } from '@/lib/catalogue/slug';
import { SAVED_OFFERS_REQUEST_LIMIT } from '@/lib/saved/limits';

/**
 * Saved page server action (FREEJ-28). The browser keeps only slugs; this resolves them to the public
 * offers they name, through the same catalogue facade the pages use (so the browser never calls FastAPI,
 * and a read goes through the one-minute Data Cache). Each slug ends up in exactly one list:
 *
 * - `offers`: published, live and public (the card shows them).
 * - `missing`: the catalogue answered and there is no such public offer any more (unknown, draft,
 *   archived, expired or ended). The Saved page drops these from the list and from the browser.
 * - `unavailable`: the catalogue could not be read for this slug. Never pruned: an outage must not
 *   empty somebody's list. The page offers a retry.
 *
 * At most `SAVED_OFFERS_REQUEST_LIMIT` slugs a call and `MAX_CONCURRENT_LOOKUPS` reads at a time, so one
 * visitor cannot fan a call out into a flood against the backend's per-address limit. Nothing here logs
 * a slug, and backend text never reaches the browser.
 */

const MAX_CONCURRENT_LOOKUPS = 6;

const INVALID_REQUEST_MESSAGE = 'We could not check your saved freebies. Please try again.';
const UNAVAILABLE_MESSAGE = 'We could not load your saved freebies just now. Please try again.';

const slugsSchema = z
  .array(z.string().max(MAX_SLUG_LENGTH).regex(SLUG_PATTERN))
  .min(1)
  .max(SAVED_OFFERS_REQUEST_LIMIT);

type Resolution = { kind: 'found'; offer: Offer } | { kind: 'missing' } | { kind: 'unavailable' };

export type SavedOffersResult =
  | { success: true; data: { offers: Offer[]; missing: string[]; unavailable: string[] } }
  | { success: false; error: string };

async function withMerchant(offer: Offer): Promise<Offer> {
  if (offer.merchant) return offer;
  const merchant = await getMerchantBySlug(offer.merchantSlug);
  return merchant ? { ...offer, merchant } : offer;
}

async function resolveSlug(slug: string, today: string): Promise<Resolution> {
  try {
    const lookup = await lookupOffer(slug);
    if (lookup.kind !== 'found') return { kind: 'missing' };
    // The facade also answers for an expired offer (so its page can say it ended): a card must not.
    if (!isLive(lookup.offer, today)) return { kind: 'missing' };
    return { kind: 'found', offer: await withMerchant(lookup.offer) };
  } catch (error) {
    if (!(error instanceof CatalogueUnavailableError)) {
      // The name only: messages can carry detail, and the slug belongs to the visitor's list.
      logger.error('Saved offer lookup failed', {
        errorName: error instanceof Error ? error.name : typeof error,
      });
    }
    return { kind: 'unavailable' };
  }
}

/** Runs `work` over `items` with a fixed number of workers, keeping the result order. */
async function mapWithLimit<T, R>(
  items: readonly T[],
  limit: number,
  work: (item: T) => Promise<R>
): Promise<R[]> {
  const results = new Array<R>(items.length);
  let next = 0;
  const worker = async () => {
    while (next < items.length) {
      const index = next;
      next += 1;
      results[index] = await work(items[index]);
    }
  };
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
  return results;
}

export async function loadSavedOffersAction(input: unknown): Promise<SavedOffersResult> {
  const parsed = slugsSchema.safeParse(input);
  if (!parsed.success) return { success: false, error: INVALID_REQUEST_MESSAGE };

  const slugs = [...new Set(parsed.data)];
  let today: string;
  try {
    today = await getToday();
  } catch (error) {
    logger.error('Saved offers could not read the date', {
      errorName: error instanceof Error ? error.name : typeof error,
    });
    return { success: false, error: UNAVAILABLE_MESSAGE };
  }

  const resolutions = await mapWithLimit(slugs, MAX_CONCURRENT_LOOKUPS, (slug) =>
    resolveSlug(slug, today)
  );

  const offers: Offer[] = [];
  const missing: string[] = [];
  const unavailable: string[] = [];
  resolutions.forEach((resolution, index) => {
    if (resolution.kind === 'found') offers.push(resolution.offer);
    else if (resolution.kind === 'missing') missing.push(slugs[index]);
    else unavailable.push(slugs[index]);
  });
  return { success: true, data: { offers, missing, unavailable } };
}
