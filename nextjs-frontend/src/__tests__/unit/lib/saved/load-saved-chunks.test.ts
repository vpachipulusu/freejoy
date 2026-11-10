import { describe, expect, it, vi } from 'vitest';
import { loadSavedChunks } from '@/lib/saved/load-saved-chunks';
import { OFFERS } from '@/lib/catalogue/fixtures';
import { toCardOffer, type CardOffer } from '@/lib/catalogue/types';

const slugs = (count: number) => Array.from({ length: count }, (_, i) => `offer-${i}`);
const cardFor = (slug: string): CardOffer => ({ ...toCardOffer(OFFERS[0]), slug, id: slug });

type Answer = { offers?: string[]; missing?: string[]; unavailable?: string[] };
const ok = ({ offers = [], missing = [], unavailable = [] }: Answer) => ({
  success: true as const,
  data: { offers: offers.map(cardFor), missing, unavailable },
});
const allFound = async (chunk: string[]) => ok({ offers: chunk });

describe('loadSavedChunks', () => {
  it('asks in chunks of the given size and gathers every answer', async () => {
    const ask = vi.fn(allFound);

    const outcome = await loadSavedChunks(slugs(60), ask, { size: 24 });

    expect(ask.mock.calls.map(([chunk]) => chunk.length)).toEqual([24, 24, 12]);
    expect(outcome.offers).toHaveLength(60);
    expect(outcome).toMatchObject({
      missing: [],
      unavailable: [],
      failed: false,
      cancelled: false,
    });
  });

  it('keeps the first chunk, reports failure and stops asking when a later chunk fails', async () => {
    const ask = vi
      .fn<(chunk: string[]) => ReturnType<typeof allFound>>()
      .mockImplementationOnce(allFound)
      .mockResolvedValueOnce({ success: false, error: 'busy' } as never);

    const outcome = await loadSavedChunks(slugs(60), ask, { size: 24 });

    expect(ask).toHaveBeenCalledTimes(2);
    expect(outcome.offers).toHaveLength(24);
    expect(outcome.failed).toBe(true);
    expect(outcome.missing).toEqual([]);
  });

  it('counts a call that throws as a failure and never throws itself', async () => {
    const ask = vi.fn(async (): Promise<never> => {
      throw new Error('network down');
    });

    await expect(loadSavedChunks(slugs(3), ask)).resolves.toMatchObject({
      failed: true,
      offers: [],
    });
  });

  it('calls a slug the answer did not account for unavailable, and ignores an offer nobody asked about', async () => {
    const ask = vi.fn(async () => ok({ offers: ['offer-1', 'stranger'], missing: [] }));

    const outcome = await loadSavedChunks(['offer-0', 'offer-1'], ask);

    expect(outcome.offers.map((offer) => offer.slug)).toEqual(['offer-1']);
    expect(outcome.unavailable).toEqual(['offer-0']);
    expect(outcome.missing).toEqual([]);
  });

  it('stops after a chunk with no offers and nothing missing, calling every later slug unavailable', async () => {
    const ask = vi.fn(async (chunk: string[]) => ok({ unavailable: chunk }));

    const outcome = await loadSavedChunks(slugs(101), ask, { size: 24 });

    expect(ask).toHaveBeenCalledTimes(1);
    expect(outcome.unavailable).toHaveLength(101);
    expect(outcome.offers).toEqual([]);
    expect(outcome.failed).toBe(false);
  });

  it('keeps asking when a chunk was all missing: that is a real answer', async () => {
    const ask = vi.fn(async (chunk: string[]) => ok({ missing: chunk }));

    const outcome = await loadSavedChunks(slugs(50), ask, { size: 24 });

    expect(ask).toHaveBeenCalledTimes(3);
    expect(outcome.missing).toHaveLength(50);
  });

  it('stops before the next call when told to, and says so', async () => {
    let alive = true;
    const ask = vi.fn(async (chunk: string[]) => {
      alive = false;
      return ok({ offers: chunk });
    });

    const outcome = await loadSavedChunks(slugs(60), ask, {
      size: 24,
      shouldContinue: () => alive,
    });

    expect(ask).toHaveBeenCalledTimes(1);
    expect(outcome.cancelled).toBe(true);
  });

  it('does nothing for an empty list', async () => {
    const ask = vi.fn(allFound);

    await expect(loadSavedChunks([], ask)).resolves.toMatchObject({ offers: [], failed: false });
    expect(ask).not.toHaveBeenCalled();
  });
});
