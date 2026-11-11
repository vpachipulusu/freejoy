/**
 * The two query markers of the commission disclosure bounce (FREEJ-60). Both are exact-match and can only make a
 * result stricter, so a forged value gains nothing.
 *
 * - `nd=1` on `/go/<id>`: the link was rendered on a page that showed no "Ad" label. The backend then refuses
 *   the click if the offer earns commission (see `respond.ts`).
 * - `ad=1` on `/freebies/<slug>`: the visitor was sent back because that page was out of date. The offer page then
 *   shows the "Ad" label, the commission sentence and a plain `/go/<id>` link whatever its cached flag says.
 *   It can only ADD a disclosure, never remove one.
 */

export const UNDISCLOSED_PARAM = 'nd';
export const FORCE_DISCLOSURE_PARAM = 'ad';
const MARKER_VALUE = '1';

/** True only for exactly one value, and that value is exactly `1`. */
export function isExactMarker(values: readonly string[]): boolean {
  return values.length === 1 && values[0] === MARKER_VALUE;
}

/**
 * Whether the offer page must show the commission disclosure. `value` is the raw Next.js `searchParams.ad`: a
 * string, an array when the parameter is repeated, or undefined. Only a single `1` forces it.
 */
export function forcesDisclosure(value: string | string[] | undefined): boolean {
  if (value === undefined) return false;
  return isExactMarker(Array.isArray(value) ? value : [value]);
}

/** The relative path of the offer page the bounce sends the visitor to. The slug must already be checked. */
export function disclosureBouncePath(slug: string): string {
  return `/freebies/${slug}?${FORCE_DISCLOSURE_PARAM}=${MARKER_VALUE}`;
}
