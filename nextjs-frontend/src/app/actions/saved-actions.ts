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
import { clientBucket } from '@/lib/middleware/client-bucket';
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
 * - a budget of backend READS, not calls: `slugs.length` is charged before any read. A visitor with a trusted
 *   address gets `KNOWN_READS_PER_MINUTE` (72) a minute (keyed on the IPv6 /64 or IPv4 address, as the backend
 *   does). With no trusted address everyone shares `SHARED_READS_PER_MINUTE` (60) a minute, so Saved can never
 *   take more than half the backend's 120 and degrades softly (a curated failure, nothing pruned) instead of
 *   one client locking the page for all. The limiter is in memory, per server instance;
 * - the first 429 from the backend, 4 unavailable results in a row (any answer resets the count), or
 *   `DEADLINE_MS` (about 8 s) overall stops the call taking new slugs; the rest come back `unavailable` with no
 *   backend call (a stale cache entry may still refresh in the background, which costs the backend one read).
 *
 * Nothing here logs a slug, and backend text never reaches the browser.
 */

const MAX_CONCURRENT_LOOKUPS = 4;
const KNOWN_READS_PER_MINUTE = 72;
const SHARED_READS_PER_MINUTE = 60;
const BUDGET_WINDOW_MS = 60_000;
const MAX_CONSECUTIVE_FAILURES = 4;
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
  // An answer for another slug is a fault in the catalogue, not a definitive "no such offer": keep the save.
  if (lookup.offer.slug !== slug) return { kind: 'unavailable' };
  // The facade also answers for an expired offer (so its page can say it ended): a card must not.
  if (!isLive(lookup.offer, today)) return { kind: 'missing' };
  return { kind: 'found', offer: toCardOffer(await withMerchant(lookup.offer)) };
}

/** Whether to stop taking new slugs: a 429, a run of failures with no answer between them, or too long. */
class Pacing {
  private stopped = false;
  private failuresInARow = 0;
  private readonly deadline = Date.now() + DEADLINE_MS;

  get shouldStop(): boolean {
    return this.stopped || Date.now() > this.deadline;
  }

  record(resolution: Resolution): void {
    this.failuresInARow = resolution.kind === 'unavailable' ? this.failuresInARow + 1 : 0;
    // A whole round's worth of failures with nothing answered between them: the backend is down.
    if (this.failuresInARow >= MAX_CONSECUTIVE_FAILURES) this.stopped = true;
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

/** The visitor's rate-limit key (IPv6 grouped by /64, IPv4-mapped as IPv4), or null with no trusted address. */
async function visitorKey(): Promise<string | null> {
  try {
    const address = await readTrustedClientIp();
    return address ? clientBucket(address) : null;
  } catch {
    return null; // no request scope
  }
}

let warnedNoClientAddress = false;

/** Takes `reads` from the visitor's budget (or the shared one); false when it does not fit. */
async function chargeReads(reads: number): Promise<boolean> {
  const key = await visitorKey();
  if (key === null && !warnedNoClientAddress && process.env.NODE_ENV === 'production') {
    warnedNoClientAddress = true;
    logger.warn(
      'Saved offers have no trusted client address, so every visitor shares one read budget; set TRUSTED_CLIENT_IP_HEADER (or use Vercel)'
    );
  }
  const allowed =
    key === null
      ? rateLimit('all', {
          scope: 'saved-shared',
          maxRequests: SHARED_READS_PER_MINUTE,
          windowMs: BUDGET_WINDOW_MS,
          cost: reads,
        })
      : rateLimit(key, {
          scope: 'saved',
          maxRequests: KNOWN_READS_PER_MINUTE,
          windowMs: BUDGET_WINDOW_MS,
          cost: reads,
        });
  if (!allowed.success) {
    // Counts only: never an address, never a slug.
    logger.warn('Saved offers refused: over the read budget', { reads, shared: key === null });
  }
  return allowed.success;
}

export async function loadSavedOffersAction(input: unknown): Promise<SavedOffersResult> {
  const parsed = slugsSchema.safeParse(input);
  if (!parsed.success) return { success: false, error: INVALID_REQUEST_MESSAGE };

  const slugs = [...new Set(parsed.data)];
  if (!(await chargeReads(slugs.length))) return { success: false, error: UNAVAILABLE_MESSAGE };

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
