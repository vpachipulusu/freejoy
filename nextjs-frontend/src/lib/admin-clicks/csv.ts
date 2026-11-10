/**
 * The merchant click report as a CSV file a retailer can open in Excel by double-click.
 *
 * Format: comma separated, UTF-8 with a byte-order mark (so Excel reads accents and dashes correctly), CRLF
 * line endings, a field quoted when it holds a comma, a semicolon, a tab, a double quote, CR or LF (quotes doubled).
 * Offer titles and merchant names are editor text, so a spreadsheet must never run a cell as a formula. A text
 * cell gets a single quote in front when its first visible character is `=`, `+`, `-` or `@` (or their full-width
 * forms), also after leading whitespace or zero-width characters, and always when it starts with a tab, CR or LF.
 * A formula character right after a `;`, a tab or a line break inside a cell also gets the quote (an Excel whose list separator
 * is a semicolon splits the cell there and would read the next part as a new cell). Counts are written as plain
 * numbers, and only when finite. Pure and client-safe.
 *
 * Rows: one per offer per day that had clicks, so the sum of the Clicks column is the total of the offers listed
 * and the total per offer is a sum by offer (no total rows, nothing is counted twice). An offer with no clicks in
 * the range appears once with an empty date and 0 clicks, so the retailer sees every listing.
 */

const BOM = String.fromCharCode(0xfeff);
const EOL = '\r\n';
// Formula characters: = + - @ and their full-width forms (U+FF1D, U+FF0B, U+FF0D, U+FF20). Skipped before them:
// whitespace (NBSP, U+3000, U+FEFF and line breaks included) and the zero-width characters U+200B-U+200D, U+2060.
const FORMULA_START = /^[\s\u200b-\u200d\u2060]*[=+\-@\uff1d\uff0b\uff0d\uff20]/;
const CONTROL_START = /^[\t\r\n]/;
const SEPARATOR_THEN_FORMULA =
  /([;\t\r\n][\s\u200b-\u200d\u2060]*)(?=[=+\-@\uff1d\uff0b\uff0d\uff20])/g;
const NEEDS_QUOTES = /[",;\t\r\n]/;
const SLUG_MAX_LENGTH = 60;
/** The largest file the export action hands back, in UTF-8 bytes. A larger report asks for a shorter range. */
export const MAX_CSV_BYTES = 4 * 1024 * 1024;
export const CSV_TOO_LARGE_MESSAGE =
  'This report is too large to download as one file. Choose a shorter date range.';

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

/** One cell: a number as plain digits (only when finite), text neutralised against formulas and quoted when it must be. */
export function csvCell(value: string | number): string {
  if (typeof value === 'number') return Number.isFinite(value) ? String(value) : '';
  const lead = CONTROL_START.test(value) || FORMULA_START.test(value) ? `'${value}` : value;
  const safe = lead.replace(SEPARATOR_THEN_FORMULA, "$1'");
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

const DAY_PATTERN = /^[0-9]{4}-[0-9]{2}-[0-9]{2}$/;

/**
 * `freejoy-clicks-<merchant slug>-<from>-to-<to>.csv`: the slug is reduced to lower-case letters, digits and hyphens
 * and a date that is not exactly `YYYY-MM-DD` becomes `date`, so the name never carries a path or other characters.
 */
export function clicksCsvFilename(slug: string, from: string, to: string): string {
  const day = (value: string) => (DAY_PATTERN.test(value) ? value : 'date');
  return `freejoy-clicks-${safeSlug(slug)}-${day(from)}-to-${day(to)}.csv`;
}

/** The size of a file in UTF-8 bytes, as it would be downloaded. */
export function csvByteLength(csv: string): number {
  return new TextEncoder().encode(csv).length;
}
