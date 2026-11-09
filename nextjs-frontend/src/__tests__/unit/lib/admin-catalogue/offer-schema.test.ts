// @vitest-environment node
import { describe, expect, it } from 'vitest';
import {
  descriptionParagraphs,
  emptyOfferFormValues,
  minorToPounds,
  offerFormSchema,
  poundsToMinor,
  type OfferFormValues,
} from '@/lib/admin-catalogue/offer-schema';
import {
  isoToLocalInput,
  localInputToIso,
  offerToFormValues,
  type OfferDetailView,
} from '@/lib/admin-catalogue/offer-view';
import { ACTION_FAILED_MESSAGE, settleAction } from '@/lib/admin-catalogue/settle-action';
import {
  availableOfferActions,
  isOfferAction,
  isOfferStatus,
  publicOfferPath,
  OFFER_STATUS_VARIANTS,
  OFFER_STATUS_ICONS,
} from '@/lib/admin-catalogue/offer-status';

function valid(overrides: Partial<OfferFormValues> = {}): OfferFormValues {
  return {
    ...emptyOfferFormValues(),
    title: 'Free coffee sample',
    summary: 'A free tasting sachet.',
    categoryId: 'cat_1',
    merchantId: 'mer_1',
    imageLabel: 'coffee',
    imageAlt: 'A coffee sachet',
    eligibility: 'UK adults.',
    availability: 'While stocks last.',
    ...overrides,
  };
}

function firstIssue(values: OfferFormValues) {
  const result = offerFormSchema.safeParse(values);
  return result.success ? null : result.error.issues[0];
}

describe('offerFormSchema', () => {
  it('accepts a complete offer and trims text', () => {
    const result = offerFormSchema.safeParse(valid({ title: '  Free coffee  ' }));
    expect(result.success).toBe(true);
    expect(result.success && result.data.title).toBe('Free coffee');
  });

  it.each([
    ['title', { title: '' }, 'Enter a title.'],
    ['title', { title: 'x'.repeat(201) }, 'Keep this to 200 characters or fewer.'],
    ['title', { title: 'two\nlines' }, 'Remove line breaks and control characters.'],
    ['summary', { summary: 'y'.repeat(301) }, 'Keep this to 300 characters or fewer.'],
    ['categoryId', { categoryId: '' }, 'Choose a category.'],
    ['merchantId', { merchantId: '' }, 'Choose a merchant.'],
    ['sourceUrl', { sourceUrl: 'http://example.com' }, 'Enter a full https:// address.'],
    ['sourceUrl', { sourceUrl: 'not a url' }, 'Enter a full https:// address.'],
    ['slug', { slug: 'Bad Slug' }, 'Use 2 to 80 lower-case letters, numbers and single hyphens.'],
    ['slug', { slug: 'a' }, 'Use 2 to 80 lower-case letters, numbers and single hyphens.'],
    ['eligibility', { eligibility: 'e'.repeat(501) }, 'Keep this to 500 characters or fewer.'],
  ])('rejects a bad %s', (path, overrides, message) => {
    const issue = firstIssue(valid(overrides as Partial<OfferFormValues>));
    expect(issue?.path[0]).toBe(path);
    expect(issue?.message).toBe(message);
  });

  it.each([
    'https://user:pass@example.com/x',
    'https://example.com:8443/x',
    'https://:secret@example.com',
  ])('rejects the source URL %s (user info or an unusual port)', (sourceUrl) => {
    expect(firstIssue(valid({ sourceUrl }))?.path[0]).toBe('sourceUrl');
  });

  it('accepts https URLs with no user info, on the default port or 443', () => {
    for (const sourceUrl of ['https://example.com/x?y=1', 'https://example.com:443/x']) {
      expect(offerFormSchema.safeParse(valid({ sourceUrl })).success).toBe(true);
    }
  });

  it('accepts blank optional fields and a clean slug', () => {
    const values = valid({ slug: 'free-coffee-sample', sourceUrl: '' });
    expect(offerFormSchema.safeParse(values).success).toBe(true);
  });

  it('requires a pounds amount when postage costs money', () => {
    expect(firstIssue(valid({ postageKind: 'COST', postageAmount: '' }))?.path[0]).toBe(
      'postageAmount'
    );
    expect(firstIssue(valid({ postageKind: 'COST', postageAmount: '2.999' }))?.path[0]).toBe(
      'postageAmount'
    );
    expect(firstIssue(valid({ postageKind: 'COST', postageAmount: '10000.01' }))?.path[0]).toBe(
      'postageAmount'
    );
    const ok = valid({ postageKind: 'COST', postageAmount: '2.99' });
    expect(offerFormSchema.safeParse(ok).success).toBe(true);
  });

  it('ignores the amount when postage is free', () => {
    const values = valid({ postageKind: 'FREE', postageAmount: 'junk' });
    expect(offerFormSchema.safeParse(values).success).toBe(true);
  });

  it('requires the end to follow the start', () => {
    const issue = firstIssue(valid({ startAt: '2026-03-02T10:00', endAt: '2026-03-02T10:00' }));
    expect(issue?.path[0]).toBe('endAt');
    const ok = valid({ startAt: '2026-03-02T10:00', endAt: '2026-03-03T10:00' });
    expect(offerFormSchema.safeParse(ok).success).toBe(true);
  });

  it('limits steps and checks each one', () => {
    const steps = Array.from({ length: 21 }, (_, i) => ({ title: `Step ${i}`, detail: 'Do it.' }));
    expect(firstIssue(valid({ steps }))?.path[0]).toBe('steps');
    expect(firstIssue(valid({ steps: [{ title: '', detail: 'x' }] }))?.path).toEqual([
      'steps',
      0,
      'title',
    ]);
  });

  it('rejects an id that is not a plain id and an oversized description', () => {
    expect(firstIssue(valid({ categoryId: '../x' }))?.path[0]).toBe('categoryId');
    expect(firstIssue(valid({ description: 'x'.repeat(50_000) }))?.path[0]).toBe('description');
  });

  it('limits the description to 20 paragraphs of 2000 characters', () => {
    const many = Array.from({ length: 21 }, (_, i) => `Paragraph ${i}`).join('\n\n');
    expect(firstIssue(valid({ description: many }))?.path[0]).toBe('description');
    expect(firstIssue(valid({ description: 'z'.repeat(2001) }))?.path[0]).toBe('description');
    expect(offerFormSchema.safeParse(valid({ description: 'One.\n\nTwo.' })).success).toBe(true);
  });
});

describe('settleAction', () => {
  it('passes a result through and turns a throw into a curated failure', async () => {
    await expect(settleAction(async () => ({ success: true as const }))).resolves.toEqual({
      success: true,
    });
    await expect(
      settleAction(async () => {
        throw new Error('SECRET network detail');
      })
    ).resolves.toEqual({ success: false, error: ACTION_FAILED_MESSAGE });
  });
});

describe('text helpers', () => {
  it('splits paragraphs on blank lines and drops empty ones', () => {
    expect(descriptionParagraphs(' One.\r\n\r\n\r\nTwo\nstill two.\n \n')).toEqual([
      'One.',
      'Two\nstill two.',
    ]);
    expect(descriptionParagraphs('   ')).toEqual([]);
  });

  it('converts pounds to minor units without float error and back', () => {
    expect(poundsToMinor('2.99')).toBe(299);
    expect(poundsToMinor('0.1')).toBe(10);
    expect(poundsToMinor('10000')).toBe(1_000_000);
    expect(poundsToMinor('1.005')).toBeNull();
    expect(poundsToMinor('-1')).toBeNull();
    expect(poundsToMinor('')).toBeNull();
    expect(minorToPounds(299)).toBe('2.99');
    expect(minorToPounds(1000)).toBe('10.00');
    expect(minorToPounds(5)).toBe('0.05');
  });

  it('round trips date-time inputs as UTC', () => {
    expect(localInputToIso('2026-03-01T09:30')).toBe('2026-03-01T09:30:00Z');
    expect(localInputToIso('2026-03-01T23:59:59')).toBe('2026-03-01T23:59:59Z');
    expect(isoToLocalInput('2026-03-01T09:30:00+00:00')).toBe('2026-03-01T09:30:00');
    expect(isoToLocalInput('2026-03-01T10:30:00+01:00')).toBe('2026-03-01T09:30:00');
    expect(isoToLocalInput(null)).toBe('');
    expect(isoToLocalInput('nonsense')).toBe('');
  });
});

describe('offerToFormValues', () => {
  const detail: OfferDetailView = {
    id: 'o1',
    title: 'T',
    slug: 'tt',
    categoryId: 'c',
    merchantId: 'm',
    offerType: 'VOUCHER',
    status: 'DRAFT',
    featured: true,
    endAt: '2026-05-01T12:00:00Z',
    lastVerifiedAt: null,
    updatedAt: '2026-01-01T00:00:00Z',
    reviewToken: 'tok-1',
    summary: 'S',
    description: ['One', 'Two'],
    imageTint: 'mint',
    imageLabel: 'L',
    imageAlt: 'A',
    sourceUrl: null,
    postageKind: 'COST',
    postageAmountMinor: 250,
    postageCurrency: 'GBP',
    requiresCard: false,
    subscriptionTerms: null,
    eligibility: 'E',
    availability: 'V',
    steps: [{ title: 'a', detail: 'b' }],
    startAt: null,
    freshness: 'POPULAR',
    publisherNotes: null,
    createdAt: '2026-01-01T00:00:00Z',
    links: [],
  };

  it('maps an offer to form values with blanks for nulls', () => {
    expect(offerToFormValues(detail)).toMatchObject({
      description: 'One\n\nTwo',
      postageAmount: '2.50',
      sourceUrl: '',
      startAt: '',
      endAt: '2026-05-01T12:00:00',
      freshness: 'POPULAR',
      publisherNotes: '',
      steps: [{ title: 'a', detail: 'b' }],
    });
  });

  it('gives a loaded offer values that pass the schema', () => {
    expect(offerFormSchema.safeParse(offerToFormValues(detail)).success).toBe(true);
  });
});

describe('offer status helpers', () => {
  it('mirrors the backend status table', () => {
    expect(availableOfferActions('DRAFT')).toEqual(['verify', 'publish', 'archive']);
    expect(availableOfferActions('PUBLISHED')).toEqual([
      'verify',
      'unpublish',
      'expire',
      'archive',
    ]);
    expect(availableOfferActions('EXPIRED')).toEqual(['verify', 'publish', 'archive']);
    expect(availableOfferActions('ARCHIVED')).toEqual([]);
    expect(availableOfferActions('UNKNOWN')).toEqual([]);
    expect(OFFER_STATUS_ICONS.DRAFT).toBeDefined();
    expect(OFFER_STATUS_ICONS.PUBLISHED).toBeUndefined();
  });

  it('tints statuses good, neutral, warn and info', () => {
    expect(OFFER_STATUS_VARIANTS).toEqual({
      PUBLISHED: 'good',
      DRAFT: 'neutral',
      EXPIRED: 'warn',
      ARCHIVED: 'info',
      UNKNOWN: 'neutral',
    });
  });

  it('knows its own values', () => {
    expect(isOfferStatus('DRAFT')).toBe(true);
    expect(isOfferStatus('draft')).toBe(false);
    expect(isOfferAction('verify')).toBe(true);
    expect(isOfferAction('delete')).toBe(false);
  });

  it('links to the public page only for a published offer', () => {
    expect(publicOfferPath('PUBLISHED', 'free-coffee')).toBe('/freebies/free-coffee');
    expect(publicOfferPath('DRAFT', 'free-coffee')).toBeNull();
    expect(publicOfferPath('EXPIRED', 'free-coffee')).toBeNull();
  });
});
