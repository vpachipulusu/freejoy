/** The commercial basis in the admin offer form's schema, labels and view mapping. */
import { describe, expect, it } from 'vitest';
import {
  COMMERCIAL_BASIS_HELP,
  COMMERCIAL_BASIS_LABELS,
  commercialBasisLabel,
} from '@/lib/admin-catalogue/offer-labels';
import {
  COMMERCIAL_BASIS_VALUES,
  emptyOfferFormValues,
  isCommercialBasis,
  offerFormSchema,
  type OfferFormValues,
} from '@/lib/admin-catalogue/offer-schema';
import { offerToFormValues, type OfferDetailView } from '@/lib/admin-catalogue/offer-view';

function valid(overrides: Partial<OfferFormValues> = {}): OfferFormValues {
  return {
    ...emptyOfferFormValues(),
    commercialBasis: 'EDITORIAL',
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

describe('commercial basis in the form schema', () => {
  it('offers exactly the three bases, in the order the form lists them', () => {
    expect(COMMERCIAL_BASIS_VALUES).toEqual(['EDITORIAL', 'AFFILIATE', 'SPONSORED']);
  });

  it('starts with nothing chosen, so there is no default to leave in place by accident', () => {
    expect(emptyOfferFormValues().commercialBasis).toBe('');
  });

  it.each(COMMERCIAL_BASIS_VALUES)('accepts %s', (basis) => {
    expect(offerFormSchema.safeParse(valid({ commercialBasis: basis })).success).toBe(true);
  });

  it('refuses an empty choice with a message on the commercialBasis field', () => {
    const result = offerFormSchema.safeParse(valid({ commercialBasis: '' }));

    expect(result.success).toBe(false);
    expect(result.error?.issues).toContainEqual(
      expect.objectContaining({
        path: ['commercialBasis'],
        message: 'Choose the commercial basis.',
      })
    );
  });

  it('refuses a value that is not one of the three', () => {
    const result = offerFormSchema.safeParse({ ...valid(), commercialBasis: 'PAID' });

    expect(result.success).toBe(false);
  });

  it('knows a basis when it sees one', () => {
    expect(isCommercialBasis('SPONSORED')).toBe(true);
    expect(['', 'sponsored', 'PAID', null, 3].some(isCommercialBasis)).toBe(false);
  });
});

describe('commercial basis labels and help', () => {
  it('gives every option a label and its own one-line help', () => {
    const help = COMMERCIAL_BASIS_VALUES.map((value) => COMMERCIAL_BASIS_HELP[value]);

    expect(COMMERCIAL_BASIS_VALUES.map(commercialBasisLabel)).toEqual([
      'Editorial',
      'Affiliate',
      'Sponsored',
    ]);
    expect(help.every((line) => line.length > 10 && !line.includes('\n'))).toBe(true);
    expect(new Set(help).size).toBe(3);
  });

  it('says only the sponsored option shows a label to visitors', () => {
    expect(COMMERCIAL_BASIS_HELP.SPONSORED).toMatch(/"Sponsored" label/);
    expect(COMMERCIAL_BASIS_HELP.EDITORIAL).not.toMatch(/label/);
    expect(COMMERCIAL_BASIS_HELP.AFFILIATE).not.toMatch(/label/);
  });

  it('falls back to the sent value for a basis this UI does not know', () => {
    expect(commercialBasisLabel('SOMETHING_NEW')).toBe('SOMETHING_NEW');
    expect(COMMERCIAL_BASIS_LABELS.EDITORIAL).toBe('Editorial');
  });
});

describe('offerToFormValues and the basis', () => {
  const detail = (commercialBasis: string): OfferDetailView =>
    ({
      id: 'o1',
      title: 'T',
      slug: 'tt',
      categoryId: 'c',
      merchantId: 'm',
      offerType: 'VOUCHER',
      commercialBasis,
      status: 'DRAFT',
      featured: false,
      endAt: null,
      lastVerifiedAt: null,
      updatedAt: '2026-01-01T00:00:00Z',
      importProvider: null,
      sourceChangedAt: null,
      provenance: null,
      reviewToken: 'tok',
      summary: 'S',
      description: [],
      imageTint: 'mint',
      imageLabel: 'L',
      imageAlt: 'A',
      sourceUrl: null,
      postageKind: 'FREE',
      postageAmountMinor: null,
      postageCurrency: null,
      requiresCard: false,
      subscriptionTerms: null,
      eligibility: 'E',
      availability: 'V',
      steps: [],
      startAt: null,
      freshness: null,
      publisherNotes: null,
      createdAt: '2026-01-01T00:00:00Z',
      links: [],
      needsBasisConfirmation: false,
    }) as OfferDetailView;

  it.each(COMMERCIAL_BASIS_VALUES)('selects the saved basis %s', (basis) => {
    expect(offerToFormValues(detail(basis)).commercialBasis).toBe(basis);
  });

  it('selects nothing for a basis this UI does not know, so the editor has to choose', () => {
    expect(offerToFormValues(detail('SOMETHING_NEW')).commercialBasis).toBe('');
  });
});
