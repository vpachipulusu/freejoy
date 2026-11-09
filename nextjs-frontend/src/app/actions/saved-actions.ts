'use server';

import { z } from 'zod';
import { readTrustedClientIp } from '@/lib/api/server';
import { logger } from '@/lib/logger';
import {
  CatalogueRateLimitedError,
  CatalogueUnavailableError,
  getMerchantBySlug,
  getToday,
  isLive,
  lookupOffer,
  toCardOffer,
  type CardOffer,
  type Offer,
} from '@/lib/catalogue';
import { MAX_SLUG_LENGTH, SLUG_PATTERN } from '@/lib/catalogue/slug';
import { SAVED_OFFERS_REQUEST_LIMIT } from '@/lib/saved/limits';
import { rateLimit } from '@/lib/middleware/rate-limit';

/**
 * Saved page server action (FREEJ-28). The browser keeps only slugs; this resolves them to the public
 * offers they name, through the same catalogue facade the pages use (so the browser never calls FastAPI,
 * and a read goes through the one-minute Data Cache). Each slug ends up in exactly one list:
 *
 * - `offers`: published, live and public, as the card-shaped projection the card needs and nothing more.
 * - `missing`: the catalogue gave a definitive answer that there is no such public offer (not found, ended,
 *   expired, or an answer for another slug). The Saved page drops these from the list and from the browser.
 * - `unavailable`: anything else: an outage, a 429, a record this site cannot read yet, or a slug this call
 *   chose not to ask about. Never pruned: pruning is irreversible, so only a definitive answer may do it.
 *
 * Protecting the backend (this action is public and unauthenticated; the backend allows 120 reads a minute
 * per address, shared by all visitors when no trusted client-IP header is configured):
 * - at most `SAVED_OFFERS_REQUEST_LIMIT` (24) slugs a call and `MAX_CONCURRENT_LOOKUPS` (4) reads at a time;
 * - at most `CALLS_PER_MINUTE` (6) calls a minute per visitor address (one shared bucket when there is no
 *   trusted address header), refused before any read;
 * - the first 429 from the backend, a first full round in which every read failed, or `DEADLINE_MS` (about
 *   8 s) overall stops the call taking new slugs; the rest come back `unavailable` with no backend call.
 *
 * Nothing here logs a slug, and backend text never reaches the browser.
 */

const MAX_CONCURRENT_LOOKUPS = 4;
const CALLS_PER_MINUTE = 6;
const DEADLINE_MS = 8_000;

const INVALID_REQUEST_MESSAGE = 'We could not check your saved freebies. Please try again.';
const UNAVAILABLE_MESSAGE = 'We could not load your saved freebies just now. Please try again.';

const slugsSchema = z
  .array(z.string().max(MAX_SLUG_LENGTH).regex(SLUG_PATTERN))
  .min(1)
  .max(SAVED_OFFERS_REQUEST_LIMIT);

type Resolution =
  | { kind: 'found'; offer: CardOffer }
  | { kind: 'missing' }
  | { kind: 'unavailable' };

export type SavedOffersResult =
  | { success: true; data: { offers: CardOffer[]; missing: string[]; unavailable: string[] } }
  | { success: false; error: string };

async function withMerchant(offer: Offer): Promise<Offer> {
  if (offer.merchant) return offer;
  const merchant = await getMerchantBySlug(offer.merchantSlug);
  return merchant ? { ...offer, merchant } : offer;
}

async function resolveSlug(slug: string, today: string): Promise<Resolution> {
  const lookup = await lookupOffer(slug);
  if (lookup.kind === 'not_found' || lookup.kind === 'ended') return { kind: 'missing' };
  if (lookup.kind === 'unreadable') return { kind: 'unavailable' };
  // The answer must be for the slug asked, and the facade also answers for an expired offer (so its page can
  // say it ended): a card must not.
  if (lookup.offer.slug !== slug || !isLive(lookup.offer, today)) return { kind: 'missing' };
  return { kind: 'found', offer: toCardOffer(await withMerchant(lookup.offer)) };
}

/** Whether to stop taking new slugs: a 429, a whole first round of failures, or too long. */
class Pacing {
  private stopped = false;
  private completed = 0;
  private answered = 0;
  private readonly deadline = Date.now() + DEADLINE_MS;

  get shouldStop(): boolean {
    return this.stopped || Date.now() > this.deadline;
  }

  record(resolution: Resolution): void {
    this.completed += 1;
    if (resolution.kind !== 'unavailable') this.answered += 1;
    // A full round (as many reads as run at once) with no answer at all: the backend is down.
    if (this.answered === 0 && this.completed >= MAX_CONCURRENT_LOOKUPS) this.stopped = true;
  }

  rateLimited(): void {
    this.stopped = true;
  }
}

async function resolveAll(slugs: readonly string[], today: string): Promise<Resolution[]> {
  const pacing = new Pacing();
  const results = new Array<Resolution>(slugs.length);
  let next = 0;

  const worker = async () => {
    while (next < slugs.length) {
      const index = next;
      next += 1;
      if (pacing.shouldStop) {
        results[index] = { kind: 'unavailable' };
        continue;
      }
      try {
        results[index] = await resolveSlug(slugs[index], today);
      } catch (error) {
        if (error instanceof CatalogueRateLimitedError) pacing.rateLimited();
        else if (!(error instanceof CatalogueUnavailableError)) {
          // The name only: messages can carry detail, and the slug belongs to the visitor's list.
          logger.error('Saved offer lookup failed', {
            errorName: error instanceof Error ? error.name : typeof error,
          });
        }
        results[index] = { kind: 'unavailable' };
      }
      pacing.record(results[index]);
    }
  };
  await Promise.all(Array.from({ length: Math.min(MAX_CONCURRENT_LOOKUPS, slugs.length) }, worker));
  return results;
}

async function visitorKey(): Promise<string> {
  try {
    return (await readTrustedClientIp()) ?? 'unknown';
  } catch {
    return 'unknown'; // no request scope
  }
}

export async function loadSavedOffersAction(input: unknown): Promise<SavedOffersResult> {
  const parsed = slugsSchema.safeParse(input);
  if (!parsed.success) return { success: false, error: INVALID_REQUEST_MESSAGE };

  const allowed = rateLimit(await visitorKey(), {
    scope: 'saved',
    maxRequests: CALLS_PER_MINUTE,
    windowMs: 60_000,
  });
  if (!allowed.success) return { success: false, error: UNAVAILABLE_MESSAGE };

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

  const resolutions = await resolveAll(slugs, today);

  const offers: CardOffer[] = [];
  const missing: string[] = [];
  const unavailable: string[] = [];
  resolutions.forEach((resolution, index) => {
    if (resolution.kind === 'found') offers.push(resolution.offer);
    else if (resolution.kind === 'missing') missing.push(slugs[index]);
    else unavailable.push(slugs[index]);
  });
  return { success: true, data: { offers, missing, unavailable } };
}
