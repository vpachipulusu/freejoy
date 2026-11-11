import { describe, expect, it } from 'vitest';
import { buildProposalRows, NONE } from '@/lib/admin-catalogue/partner-proposal';
import type { OfferDetailView } from '@/lib/admin-catalogue/offer-view';

const OFFER = {
  title: 'Editor title',
  summary: 'Summary',
  description: ['One'],
  offerType: 'FREE_SAMPLE',
  categoryId: 'c1',
  postageKind: 'FREE',
  postageAmountMinor: null,
  postageCurrency: null,
  requiresCard: false,
  subscriptionTerms: null,
  eligibility: 'UK',
  availability: 'Stocks',
  steps: [{ title: 'Go', detail: 'Do' }],
  startAt: null,
  endAt: null,
  links: [
    { id: 'l1', provider: 'direct', destinationUrl: 'https://shop.example.com/a', active: true },
  ],
} as unknown as OfferDetailView;

const PAYLOAD = {
  title: 'Editor title',
  summary: 'Summary',
  description: ['One'],
  offer_type: 'FREE_SAMPLE',
  category_id: 'c1',
  postage_kind: 'FREE',
  postage_amount_minor: null,
  postage_currency: null,
  requires_card: false,
  subscription_terms: null,
  eligibility: 'UK',
  availability: 'Stocks',
  steps: [{ title: 'Go', detail: 'Do' }],
  start_at: null,
  end_at: null,
  destination_url: 'https://shop.example.com/a',
};

const names = (id: string) => (id === 'c1' ? 'Home' : id === 'c2' ? 'Beauty' : 'Unknown');
const date = (value: string) => value.slice(0, 10);

function rows(changes: Record<string, unknown> = {}, offer: OfferDetailView = OFFER) {
  return buildProposalRows(offer, { ...PAYLOAD, ...changes }, names, date);
}

const changedKeys = (list: ReturnType<typeof rows>) =>
  list.filter((r) => r.changed).map((r) => r.key);

describe('buildProposalRows', () => {
  it('lists every field in reading order and flags nothing when the proposal equals the offer', () => {
    const list = rows();

    expect(list.map((r) => r.key)).toEqual([
      'title',
      'summary',
      'description',
      'offer_type',
      'category',
      'postage',
      'requires_card',
      'subscription_terms',
      'eligibility',
      'availability',
      'steps',
      'start_at',
      'end_at',
      'link',
    ]);
    expect(changedKeys(list)).toEqual([]);
  });

  it('flags exactly the fields that differ and shows both sides', () => {
    const list = rows({
      title: 'Retailer title',
      category_id: 'c2',
      requires_card: true,
      description: ['One', 'Two'],
      destination_url: 'https://shop.example.com/b',
    });

    expect(changedKeys(list)).toEqual([
      'title',
      'description',
      'category',
      'requires_card',
      'link',
    ]);
    const category = list.find((r) => r.key === 'category');
    expect([category?.current, category?.proposed]).toEqual([['Home'], ['Beauty']]);
    expect(list.find((r) => r.key === 'description')?.proposed).toEqual(['One', 'Two']);
    expect(list.find((r) => r.key === 'requires_card')?.proposed).toEqual(['Yes']);
  });

  it('shows an empty value as None on either side and treats blank as empty', () => {
    const list = rows({ subscription_terms: 'Cancel any time' });
    const terms = list.find((r) => r.key === 'subscription_terms');

    expect(terms).toMatchObject({ current: [NONE], proposed: ['Cancel any time'], changed: true });
    expect(rows({ description: ['  '] }, { ...OFFER, description: [] } as never)[2].changed).toBe(
      false
    );
  });

  it('formats costed postage and dates', () => {
    const list = rows({
      postage_kind: 'COST',
      postage_amount_minor: 199,
      postage_currency: 'GBP',
      end_at: '2099-01-01T00:00:00+00:00',
    });

    expect(list.find((r) => r.key === 'postage')?.proposed).toEqual([
      'Postage costs money: GBP 1.99',
    ]);
    expect(list.find((r) => r.key === 'end_at')?.proposed).toEqual(['2099-01-01']);
  });

  it('compares the link only when the offer has exactly one direct link', () => {
    const two = {
      ...OFFER,
      links: [...OFFER.links, { id: 'l2', provider: 'direct', destinationUrl: 'x', active: true }],
    } as unknown as OfferDetailView;

    const link = rows({}, two).find((r) => r.key === 'link');

    expect(link?.changed).toBe(true);
    expect(link?.current[0]).toMatch(/Not changed here/);
  });

  it('survives a malformed payload without throwing and never interprets text as markup', () => {
    const list = buildProposalRows(
      OFFER,
      { title: '<img src=x onerror=alert(1)>', steps: 'nope', description: [1, 'ok'] },
      names,
      date
    );

    expect(list.find((r) => r.key === 'title')?.proposed).toEqual(['<img src=x onerror=alert(1)>']);
    expect(list.find((r) => r.key === 'steps')?.proposed).toEqual([NONE]);
    expect(list.find((r) => r.key === 'description')?.proposed).toEqual(['ok']);
  });
});
