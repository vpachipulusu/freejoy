import { describe, expect, it } from 'vitest';
import { formatAdminDateTime } from '@/app/(admin)/admin/_components/format-admin-date';
import { OFFER_FIELD_LABELS } from '@/lib/admin-catalogue/offer-labels';
import { buildProposalRows, NONE, UNREADABLE } from '@/lib/admin-catalogue/partner-proposal';
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

function rows(changes: Record<string, unknown> = {}, offer: OfferDetailView = OFFER) {
  return buildProposalRows(offer, { ...PAYLOAD, ...changes }, names, formatAdminDateTime);
}

const changedKeys = (list: ReturnType<typeof rows>) =>
  list.filter((r) => r.changed).map((r) => r.key);
const find = (list: ReturnType<typeof rows>, key: string) => list.find((r) => r.key === key);
const withOffer = (changes: Record<string, unknown>) =>
  ({ ...OFFER, ...changes }) as unknown as OfferDetailView;

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
    const category = find(list, 'category');
    expect([category?.current, category?.proposed]).toEqual([['Home'], ['Beauty']]);
    expect(find(list, 'description')?.proposed).toEqual(['One', 'Two']);
    expect(find(list, 'requires_card')?.proposed).toEqual(['Yes']);
  });

  it('uses the edit form labels for the fields the form names', () => {
    const list = rows();

    expect(find(list, 'eligibility')?.label).toBe(OFFER_FIELD_LABELS.eligibility);
    expect(find(list, 'requires_card')?.label).toBe(OFFER_FIELD_LABELS.requiresCard);
    expect(find(list, 'start_at')?.label).toBe(OFFER_FIELD_LABELS.startAt);
    expect(find(list, 'end_at')?.label).toBe(OFFER_FIELD_LABELS.endAt);
    expect(OFFER_FIELD_LABELS).toEqual({
      eligibility: 'Who can claim',
      requiresCard: 'Needs a payment card to claim',
      startAt: 'Starts (UTC)',
      endAt: 'Ends (UTC)',
    });
  });

  it('shows an empty value as None on either side and treats blank as empty', () => {
    const list = rows({ subscription_terms: 'Cancel any time' });
    const terms = find(list, 'subscription_terms');

    expect(terms).toMatchObject({ current: [NONE], proposed: ['Cancel any time'], changed: true });
    expect(rows({ description: ['  '] }, withOffer({ description: [] }))[2].changed).toBe(false);
  });

  it('formats costed postage as money with the shared formatter', () => {
    const list = rows({
      postage_kind: 'COST',
      postage_amount_minor: 199,
      postage_currency: 'GBP',
    });

    expect(find(list, 'postage')?.proposed).toEqual(['Postage costs money: £1.99']);
    expect(find(list, 'postage')?.changed).toBe(true);
  });

  describe('dates', () => {
    const live = withOffer({ endAt: '2026-12-31T09:00:00Z', startAt: '2026-06-01T00:00:00Z' });

    it('are changed when only the time of day moves, and both sides show the time and UTC', () => {
      const list = rows(
        { end_at: '2026-12-31T23:59:00+00:00', start_at: '2026-06-01T00:00:00Z' },
        live
      );

      expect(changedKeys(list)).toEqual(['end_at']);
      const end = find(list, 'end_at');
      expect(end?.current).toEqual(['31 Dec 2026, 09:00 UTC']);
      expect(end?.proposed).toEqual(['31 Dec 2026, 23:59 UTC']);
      expect(end?.current[0]).toMatch(/UTC$/);
    });

    it.each(['2026-12-31T09:00:00Z', '2026-12-31T09:00:00+00:00', '2026-12-31T04:00:00-05:00'])(
      'are not changed when the same instant is spelled %s',
      (spelled) => {
        const list = rows({ end_at: spelled, start_at: '2026-06-01T00:00:00Z' }, live);

        expect(changedKeys(list)).toEqual([]);
      }
    );

    it('are changed between nothing and a date, with None on the empty side', () => {
      const add = rows({ end_at: '2027-01-01T00:00:00Z' });
      const clear = rows({ end_at: null }, live);

      expect(find(add, 'end_at')).toMatchObject({
        changed: true,
        current: [NONE],
        proposed: ['1 Jan 2027, 00:00 UTC'],
      });
      expect(find(clear, 'end_at')).toMatchObject({
        changed: true,
        current: ['31 Dec 2026, 09:00 UTC'],
        proposed: [NONE],
      });
    });

    it('show a date the formatter cannot read as it was sent, and compare such text as text', () => {
      const list = rows({ end_at: 'soon' }, withOffer({ endAt: 'soon' }));
      const other = rows({ end_at: 'later' }, withOffer({ endAt: 'soon' }));

      expect(changedKeys(list)).toEqual([]);
      expect(find(other, 'end_at')?.proposed).toEqual(['later']);
      expect(changedKeys(other)).toEqual(['end_at']);
    });
  });

  describe('category', () => {
    it('is compared by id, so two unnamed categories still differ', () => {
      const list = buildProposalRows(
        withOffer({ categoryId: 'x1' }),
        { ...PAYLOAD, category_id: 'x2' },
        () => 'Unknown category',
        formatAdminDateTime
      );

      expect(find(list, 'category')).toMatchObject({
        changed: true,
        current: ['Unknown category'],
        proposed: ['Unknown category'],
      });
    });
  });

  describe('the retailer link', () => {
    const two = withOffer({
      links: [
        ...OFFER.links,
        {
          id: 'l2',
          provider: 'direct',
          destinationUrl: 'https://shop.example.com/z',
          active: true,
        },
      ],
    });

    it('compares against the one direct link and has no note', () => {
      const link = find(rows({ destination_url: 'https://shop.example.com/b' }), 'link');

      expect(link).toMatchObject({ changed: true, applies: true });
      expect(link?.note).toBeUndefined();
      expect(find(rows(), 'link')).toMatchObject({ changed: false, applies: true });
    });

    it('is never a change with two direct links, and says it will not be applied', () => {
      const list = rows({ destination_url: 'https://shop.example.com/b' }, two);

      expect(changedKeys(list)).toEqual([]);
      const link = find(list, 'link');
      expect(link).toMatchObject({ applies: false, changed: false });
      expect(link?.note).toBe(
        "The retailer's link will not be applied because this offer has 2 direct links. Update the link yourself."
      );
      expect(link?.proposed).toEqual(['https://shop.example.com/b']);
    });

    it('says the offer has no direct link when it has none', () => {
      const list = rows(
        { destination_url: 'https://shop.example.com/b' },
        withOffer({ links: [] })
      );

      expect(find(list, 'link')?.note).toMatch(/has no direct link/);
      expect(changedKeys(list)).toEqual([]);
    });

    it('has no note when the retailer link is already one of the direct links', () => {
      const list = rows({ destination_url: 'https://shop.example.com/z' }, two);

      expect(find(list, 'link')?.note).toBeUndefined();
      expect(changedKeys(list)).toEqual([]);
    });

    it('does not count a non-direct link', () => {
      const awin = withOffer({
        links: [
          { id: 'l9', provider: 'awin', destinationUrl: 'https://x.example/a', active: true },
        ],
      });

      expect(find(rows({}, awin), 'link')?.applies).toBe(false);
    });
  });

  describe('a malformed payload', () => {
    it('never throws and never interprets text as markup', () => {
      const list = buildProposalRows(
        OFFER,
        { title: '<img src=x onerror=alert(1)>', steps: 'nope', description: [1, 'ok'] },
        names,
        formatAdminDateTime
      );

      expect(find(list, 'title')?.proposed).toEqual(['<img src=x onerror=alert(1)>']);
      expect(find(list, 'steps')?.proposed).toEqual([UNREADABLE]);
      expect(find(list, 'description')?.proposed).toEqual([UNREADABLE, 'ok']);
    });

    it('treats names that exist on Object.prototype as plain text', () => {
      const list = rows({
        postage_kind: 'constructor',
        offer_type: '__proto__',
        category_id: 'toString',
        steps: [null, 5, 'x'],
        destination_url: 7,
      });

      expect(find(list, 'postage')?.proposed).toEqual(['constructor']);
      expect(find(list, 'offer_type')?.proposed).toEqual(['__proto__']);
      expect(find(list, 'category')?.proposed).toEqual(['Unknown']);
      expect(find(list, 'steps')?.proposed).toEqual([UNREADABLE, UNREADABLE, UNREADABLE]);
      expect(find(list, 'link')?.proposed).toEqual([UNREADABLE]);
      expect(changedKeys(list)).toEqual(
        expect.arrayContaining(['postage', 'offer_type', 'category', 'steps', 'link'])
      );
    });

    it('marks a present value of the wrong type as unreadable, never as None', () => {
      const list = rows({
        title: 42,
        requires_card: 'yes',
        start_at: 7,
        end_at: {},
        postage_kind: 5,
        eligibility: ['x'],
      });

      for (const key of [
        'title',
        'requires_card',
        'start_at',
        'end_at',
        'postage',
        'eligibility',
      ]) {
        expect(find(list, key)?.proposed).toEqual([UNREADABLE]);
        expect(find(list, key)?.changed).toBe(true);
      }
    });

    it('marks a costed postage with a text amount as unreadable', () => {
      const list = rows({
        postage_kind: 'COST',
        postage_amount_minor: '199',
        postage_currency: 'GBP',
      });

      expect(find(list, 'postage')?.proposed).toEqual([UNREADABLE]);
    });

    it('still shows None for a value that is simply missing or null', () => {
      const list = buildProposalRows(OFFER, {}, names, formatAdminDateTime);

      expect(find(list, 'title')?.proposed).toEqual([NONE]);
      expect(find(list, 'requires_card')?.proposed).toEqual([NONE]);
      expect(find(list, 'steps')?.proposed).toEqual([NONE]);
    });
  });
});
