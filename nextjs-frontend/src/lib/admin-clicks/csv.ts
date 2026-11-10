/**
 * The merchant click report as a CSV file a retailer can open in Excel by double-click.
 *
 * Format: comma separated, UTF-8 with a byte-order mark (so Excel reads accents and dashes correctly), CRLF
 * line endings, a field quoted when it holds a comma, a double quote, CR or LF (quotes doubled). A text cell whose
 * first character is `=`, `+`, `-`, `@`, tab or carriage return gets a single quote in front so a spreadsheet shows
 * it as text instead of running it as a formula: offer titles are editor text. Counts are written as plain
 * numbers. Pure and client-safe.
 *
 * Rows: one per offer per day that had clicks, so the sum of the Clicks column is the total of the offers listed
 * and the total per offer is a sum by offer (no total rows, nothing is counted twice). An offer with no clicks in
 * the range appears once with an empty date and 0 clicks, so the retailer sees every listing.
 */

const BOM = String.fromCharCode(0xfeff);
const EOL = '\r\n';
const FORMULA_START = /^[=+\-@\t\r]/;
const NEEDS_QUOTES = /[",\r\n]/;
const SLUG_MAX_LENGTH = 60;

export const CSV_HEADER = [
  'Merchant',
  'Offer title',
  'Offer slug',
  'Date (UTC)',
  'Clicks',
] as const;

/** The part of the API report the file needs. */
export type ClicksCsvReport = {
  merchant: { name: string; slug: string };
  from_date: string;
  to_date: string;
  offers: { slug: string; title: string; days: { date: string; clicks: number }[] }[];
};

/** One cell: a number as plain digits, text neutralised against formulas and quoted when it must be. */
export function csvCell(value: string | number): string {
  if (typeof value === 'number') return String(value);
  const safe = FORMULA_START.test(value) ? `'${value}` : value;
  return NEEDS_QUOTES.test(safe) ? `"${safe.replaceAll('"', '""')}"` : safe;
}

function row(cells: (string | number)[]): string {
  return cells.map(csvCell).join(',') + EOL;
}

export function buildClicksCsv(report: ClicksCsvReport): string {
  const merchant = report.merchant.name;
  const lines = report.offers.flatMap((offer) =>
    offer.days.length === 0
      ? [row([merchant, offer.title, offer.slug, '', 0])]
      : offer.days.map((day) => row([merchant, offer.title, offer.slug, day.date, day.clicks]))
  );
  return BOM + row([...CSV_HEADER]) + lines.join('');
}

function safeSlug(slug: string): string {
  const cleaned = slug
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, SLUG_MAX_LENGTH)
    .replace(/-+$/, '');
  return cleaned === '' ? 'merchant' : cleaned;
}

/** `freejoy-clicks-<merchant slug>-<from>-to-<to>.csv`, the slug reduced to lower-case letters, digits and hyphens. */
export function clicksCsvFilename(slug: string, from: string, to: string): string {
  return `freejoy-clicks-${safeSlug(slug)}-${from}-to-${to}.csv`;
}
