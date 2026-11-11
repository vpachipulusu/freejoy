import { z } from 'zod';

/**
 * The date range of the admin merchant click report. Client-safe: shared by the page's URL parser, the server
 * action's input schema and the API wrapper. Dates are UTC calendar days written `YYYY-MM-DD`, both ends
 * included; the backend enforces the same rules (422 otherwise).
 */

/** The longest range the API accepts, counting both ends. */
export const MAX_CLICK_RANGE_DAYS = 366;
/** The range the form starts with: the last 30 days, today included. */
export const DEFAULT_CLICK_RANGE_DAYS = 30;

/** The first day the API accepts. */
const EARLIEST_DAY = '2000-01-01';
const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;
const ID_PATTERN = /^[A-Za-z0-9_-]{1,128}$/;
const MS_PER_DAY = 86_400_000;

export const RANGE_ORDER_MESSAGE = 'The start date must be on or before the end date.';
export const RANGE_LENGTH_MESSAGE = `The range can cover at most ${MAX_CLICK_RANGE_DAYS} days.`;

export type ClickRange = { from: string; to: string };

/** A real calendar day in the years 2000 to 2999, the API's window. */
function isRealDate(value: string): boolean {
  const parsed = new Date(`${value}T00:00:00Z`);
  const year = parsed.getUTCFullYear();
  return (
    !Number.isNaN(parsed.getTime()) &&
    parsed.toISOString().startsWith(value) &&
    year >= 2000 &&
    year <= 2999
  );
}

function dayNumber(date: string): number {
  return Math.floor(Date.parse(`${date}T00:00:00Z`) / MS_PER_DAY);
}

/** The number of UTC days from `from` to `to`, both included. */
export function daysInclusive(from: string, to: string): number {
  return dayNumber(to) - dayNumber(from) + 1;
}

/** `date` moved by `days` (negative to go back), as `YYYY-MM-DD`. */
export function addDays(date: string, days: number): string {
  return new Date((dayNumber(date) + days) * MS_PER_DAY).toISOString().slice(0, 10);
}

function today(now: Date): string {
  return now.toISOString().slice(0, 10);
}

export function defaultClickRange(now: Date): ClickRange {
  const to = today(now);
  return { from: addDays(to, -(DEFAULT_CLICK_RANGE_DAYS - 1)), to };
}

/** Why a pair of valid dates is not a usable range, or undefined when it is. */
export function clickRangeError(from: string, to: string): string | undefined {
  if (from > to) return RANGE_ORDER_MESSAGE;
  if (daysInclusive(from, to) > MAX_CLICK_RANGE_DAYS) return RANGE_LENGTH_MESSAGE;
  return undefined;
}

const dateSchema = z.string().regex(DATE_PATTERN).refine(isRealDate);
const merchantIdSchema = z.string().regex(ID_PATTERN);

/** Input of the CSV export action: what the form holds, validated again on the server. */
export const exportClicksInputSchema = z
  .object({ merchantId: merchantIdSchema, from: dateSchema, to: dateSchema })
  .strict()
  .superRefine((value, context) => {
    const message = clickRangeError(value.from, value.to);
    if (message) context.addIssue({ code: 'custom', path: ['to'], message });
  });

export type ExportClicksInput = z.infer<typeof exportClicksInputSchema>;

export type ClicksField = 'merchant' | 'from' | 'to';

export type ClicksQuery = { merchant: string | undefined; from: string; to: string };

export type ClicksParseResult = {
  query: ClicksQuery;
  /**
   * True when a date in the link was rejected or the range was refused: no report runs for a range nobody asked
   * for, and the form shows the default range.
   */
  rangeRefused: boolean;
  /** Fixed notices for values that were dropped. Never contains the rejected value. */
  notices: string[];
  invalid: Set<ClicksField>;
};

type RawParams = Record<string, string | string[] | undefined>;

const NOTICES: Record<ClicksField | 'range', string> = {
  merchant: 'The merchant in the link is not valid and was ignored. Choose one from the list.',
  from: 'The start date in the link is not a valid date, so no report was run. Pick a date.',
  to: 'The end date in the link is not a valid date, so no report was run. Pick a date.',
  range: `The dates in the link are not a valid range, so no report was run. The start must be on or before the end, at most ${MAX_CLICK_RANGE_DAYS} days apart.`,
};

/** The id of the notice that explains a rejected field, for `aria-describedby`. */
export function clicksNoticeId(
  field: ClicksField,
  { notices, invalid }: Pick<ClicksParseResult, 'notices' | 'invalid'>
): string | undefined {
  if (!invalid.has(field)) return undefined;
  return notices.includes(NOTICES[field]) ? `clicks-notice-${field}` : 'clicks-notice-range';
}

/** The id of a notice's list item, matching `clicksNoticeId`. */
export function clicksNoticeItemId(notice: string): string {
  const field = (Object.keys(NOTICES) as (ClicksField | 'range')[]).find(
    (key) => NOTICES[key] === notice
  );
  return `clicks-notice-${field ?? 'other'}`;
}

function present(value: string | string[] | undefined): string | string[] | undefined {
  return typeof value === 'string' && value.trim() === '' ? undefined : value;
}

function parseField(
  raw: RawParams,
  key: ClicksField,
  schema: z.ZodType<string>,
  notices: string[],
  invalid: Set<ClicksField>
): string | undefined {
  const value = present(raw[key]);
  if (value === undefined) return undefined;
  const parsed = schema.safeParse(typeof value === 'string' ? value.trim() : value);
  if (parsed.success) return parsed.data;
  notices.push(NOTICES[key]);
  invalid.add(key);
  return undefined;
}

const earlier = (a: string, b: string) => (a < b ? a : b);

/** A missing end is filled from the other one, inside the limit and never past today. */
function completeRange(from: string | undefined, to: string | undefined, now: Date): ClickRange {
  if (from && to) return { from, to };
  if (from) {
    const end = earlier(today(now), addDays(from, MAX_CLICK_RANGE_DAYS - 1));
    return { from, to: end < from ? from : end };
  }
  if (to) {
    const start = addDays(to, -(DEFAULT_CLICK_RANGE_DAYS - 1));
    return { from: start < EARLIEST_DAY ? EARLIEST_DAY : start, to };
  }
  return defaultClickRange(now);
}

/**
 * Parses the click report's URL parameters (`merchant`, `from`, `to`). A value that fails validation is dropped
 * with a fixed notice, never echoed; a reversed or over-long range falls back to the default range.
 */
export function parseClicksQuery(raw: RawParams, now: Date): ClicksParseResult {
  const notices: string[] = [];
  const invalid = new Set<ClicksField>();
  const merchant = parseField(raw, 'merchant', merchantIdSchema, notices, invalid);
  const from = parseField(raw, 'from', dateSchema, notices, invalid);
  const to = parseField(raw, 'to', dateSchema, notices, invalid);
  let range = completeRange(from, to, now);
  if (clickRangeError(range.from, range.to) !== undefined) {
    notices.push(NOTICES.range);
    invalid.add('from').add('to');
    range = defaultClickRange(now);
  }
  const rangeRefused = invalid.has('from') || invalid.has('to');
  return { query: { merchant, ...range }, rangeRefused, notices, invalid };
}

export const ANALYTICS_PATH = '/admin/analytics';

/** The analytics page link that carries the merchant and the range (the form's own action and Retry). */
export function clicksHref(query: ClicksQuery): string {
  const params = new URLSearchParams();
  if (query.merchant) params.set('merchant', query.merchant);
  params.set('from', query.from);
  params.set('to', query.to);
  return `${ANALYTICS_PATH}?${params.toString()}`;
}
