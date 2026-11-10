import { z } from 'zod';
import {
  isHttpsUrl,
  isParagraphText,
  isSingleLine,
  SLUG_MAX,
  SLUG_MIN,
  SLUG_PATTERN,
} from './text-rules';

/**
 * The offer form, mirrored from the admin offers API (`app/api/v1/admin_offers_schemas.py`). Values stay strings
 * as the inputs hold them; `toOfferBody` (src/lib/api/admin-offers-body.ts) turns them into the request.
 * The API validates again, so this schema only gives fast, specific messages.
 */

export const OFFER_TYPE_VALUES = [
  'FREE_SAMPLE',
  'FREE_TRIAL',
  'CASHBACK',
  'VOUCHER',
  'COMPETITION',
] as const;
/** How a listing is paid for. Required on every offer: there is no default to leave in place by accident. */
export const COMMERCIAL_BASIS_VALUES = ['EDITORIAL', 'AFFILIATE', 'SPONSORED'] as const;
export type CommercialBasisValue = (typeof COMMERCIAL_BASIS_VALUES)[number];

export function isCommercialBasis(value: unknown): value is CommercialBasisValue {
  return (COMMERCIAL_BASIS_VALUES as readonly unknown[]).includes(value);
}

export const POSTAGE_KIND_VALUES = ['FREE', 'COST', 'CHECK_TERMS'] as const;
export const FRESHNESS_VALUES = [
  'NEW_TODAY',
  'JUST_ADDED',
  'POPULAR',
  'ENDS_SOON',
  'LIMITED',
] as const;
export const TINT_VALUES = [
  'blush',
  'mint',
  'lavender',
  'sky',
  'butter',
  'cream',
  'sage',
  'sand',
] as const;

export const MAX_PARAGRAPHS = 20;
const ID_PATTERN = /^[A-Za-z0-9_-]{1,128}$/;
// 20 paragraphs of 2000 characters plus their blank-line separators.
const MAX_DESCRIPTION_LENGTH = 20 * 2000 + 19 * 4;
export const MAX_STEPS = 20;
export const MAX_POSTAGE_MINOR = 1_000_000;
const MINOR_UNITS_PER_POUND = 100;
const MAX_FRACTION_DIGITS = 2;

const POSTAGE_AMOUNT_PATTERN = /^\d{1,5}(?:\.\d{1,2})?$/;
const LOCAL_DATE_TIME_PATTERN = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?$/;
const PARAGRAPH_BREAK = /\r?\n[ \t]*\r?\n/;

const LINE_MESSAGE = 'Remove line breaks and control characters.';
const PARAGRAPH_MESSAGE = 'Remove control characters.';

function requiredLine(label: string, max: number) {
  return z
    .string()
    .trim()
    .min(1, `Enter ${label}.`)
    .max(max, `Keep this to ${max} characters or fewer.`)
    .refine(isSingleLine, LINE_MESSAGE);
}

function requiredParagraph(label: string, max: number) {
  return z
    .string()
    .trim()
    .min(1, `Enter ${label}.`)
    .max(max, `Keep this to ${max} characters or fewer.`)
    .refine(isParagraphText, PARAGRAPH_MESSAGE);
}

function optionalParagraph(max: number) {
  return z
    .string()
    .trim()
    .max(max, `Keep this to ${max} characters or fewer.`)
    .refine(isParagraphText, PARAGRAPH_MESSAGE);
}

const stepSchema = z.object({
  title: requiredLine('a step title', 120),
  detail: requiredParagraph('the step detail', 1000),
});

const optionalUrl = z
  .string()
  .trim()
  .refine((value) => value === '' || isHttpsUrl(value), 'Enter a full https:// address.');

const optionalDateTime = z
  .string()
  .refine(
    (value) => value === '' || LOCAL_DATE_TIME_PATTERN.test(value),
    'Enter a valid date and time.'
  );

const optionalSlug = z
  .string()
  .trim()
  .refine(
    (value) =>
      value === '' ||
      (value.length >= SLUG_MIN && value.length <= SLUG_MAX && SLUG_PATTERN.test(value)),
    `Use ${SLUG_MIN} to ${SLUG_MAX} lower-case letters, numbers and single hyphens.`
  );

const offerFieldsSchema = z.object({
  title: requiredLine('a title', 200),
  slug: optionalSlug,
  summary: requiredLine('a summary', 300),
  description: z.string().max(MAX_DESCRIPTION_LENGTH, 'The description is too long.'),
  categoryId: z.string().regex(ID_PATTERN, 'Choose a category.'),
  merchantId: z.string().regex(ID_PATTERN, 'Choose a merchant.'),
  offerType: z.enum(OFFER_TYPE_VALUES, 'Choose an offer type.'),
  // '' until the editor chooses: the form starts with nothing selected, and an empty choice is refused.
  commercialBasis: z.enum(['', ...COMMERCIAL_BASIS_VALUES], 'Choose the commercial basis.'),
  featured: z.boolean(),
  imageTint: z.enum(TINT_VALUES, 'Choose a tint.'),
  imageLabel: requiredLine('the picture label', 40),
  imageAlt: requiredLine('the picture description', 200),
  sourceUrl: optionalUrl,
  postageKind: z.enum(POSTAGE_KIND_VALUES, 'Choose the postage.'),
  postageAmount: z.string().trim(),
  requiresCard: z.boolean(),
  subscriptionTerms: optionalParagraph(1000),
  eligibility: requiredParagraph('who can claim it', 500),
  availability: requiredParagraph('how it is available', 500),
  steps: z.array(stepSchema).max(MAX_STEPS, `Add at most ${MAX_STEPS} steps.`),
  startAt: optionalDateTime,
  endAt: optionalDateTime,
  freshness: z.enum(['', ...FRESHNESS_VALUES]),
  publisherNotes: optionalParagraph(2000),
});

export type OfferFormValues = z.infer<typeof offerFieldsSchema>;

/** Paragraphs of the description textarea: blank-line separated, trimmed, empty ones dropped. */
export function descriptionParagraphs(text: string): string[] {
  return text
    .split(PARAGRAPH_BREAK)
    .map((part) => part.trim())
    .filter((part) => part !== '');
}

/** "12.50" to 1250. Returns null when the text is not a pounds amount with at most two decimals. */
export function poundsToMinor(text: string): number | null {
  if (!POSTAGE_AMOUNT_PATTERN.test(text)) return null;
  const [whole, fraction = ''] = text.split('.');
  const pence = fraction.padEnd(MAX_FRACTION_DIGITS, '0');
  return Number(whole) * MINOR_UNITS_PER_POUND + Number(pence);
}

export function minorToPounds(minor: number): string {
  const whole = Math.floor(minor / MINOR_UNITS_PER_POUND);
  const pence = String(minor % MINOR_UNITS_PER_POUND).padStart(MAX_FRACTION_DIGITS, '0');
  return `${whole}.${pence}`;
}

function checkCrossFields(values: OfferFormValues, ctx: z.RefinementCtx): void {
  if (values.commercialBasis === '') {
    ctx.addIssue({
      code: 'custom',
      path: ['commercialBasis'],
      message: 'Choose the commercial basis.',
    });
  }

  const paragraphs = descriptionParagraphs(values.description);
  if (paragraphs.length > MAX_PARAGRAPHS) {
    ctx.addIssue({
      code: 'custom',
      path: ['description'],
      message: `Use at most ${MAX_PARAGRAPHS} paragraphs.`,
    });
  } else if (paragraphs.some((part) => part.length > 2000 || !isParagraphText(part))) {
    ctx.addIssue({
      code: 'custom',
      path: ['description'],
      message: 'Keep each paragraph to 2000 characters, without control characters.',
    });
  }

  if (values.postageKind === 'COST') {
    const minor = poundsToMinor(values.postageAmount);
    if (minor === null || minor > MAX_POSTAGE_MINOR) {
      ctx.addIssue({
        code: 'custom',
        path: ['postageAmount'],
        message: 'Enter the postage in pounds, for example 2.99 (up to 10000.00).',
      });
    }
  }

  if (values.startAt && values.endAt && values.endAt <= values.startAt) {
    ctx.addIssue({
      code: 'custom',
      path: ['endAt'],
      message: 'The end must be after the start.',
    });
  }
}

export const offerFormSchema = offerFieldsSchema.superRefine(checkCrossFields);

export function emptyOfferFormValues(): OfferFormValues {
  return {
    title: '',
    slug: '',
    summary: '',
    description: '',
    categoryId: '',
    merchantId: '',
    offerType: 'FREE_SAMPLE',
    commercialBasis: '',
    featured: false,
    imageTint: 'blush',
    imageLabel: '',
    imageAlt: '',
    sourceUrl: '',
    postageKind: 'FREE',
    postageAmount: '',
    requiresCard: false,
    subscriptionTerms: '',
    eligibility: '',
    availability: '',
    steps: [],
    startAt: '',
    endAt: '',
    freshness: '',
    publisherNotes: '',
  };
}
