import { formatMinorUnits } from '@/lib/utils/format-money';
import { labelFor, OFFER_FIELD_LABELS, offerTypeLabel, POSTAGE_KIND_LABELS } from './offer-labels';
import type { OfferDetailView } from './offer-view';

/** One field of the comparison an editor reads before applying a retailer's update. */
export interface ProposalRow {
  key: string;
  label: string;
  /** Lines of text: a one-line field has one line, a list has one per item. Never empty (see `NONE`). */
  current: string[];
  proposed: string[];
  /** Decided on the values (instants for dates, ids for the category), never on the lines shown. */
  changed: boolean;
  /** False when applying leaves this field alone, so it is never counted as a change (see `note`). */
  applies: boolean;
  /** Said under the list when the field is not applied: what will not happen and what to do instead. */
  note?: string;
}

export const NONE = 'None';
/** Shown for a value that is present but not the kind of value it should be: never mistaken for "cleared". */
export const UNREADABLE = 'Unreadable value';

type Payload = Record<string, unknown>;

function isAbsent(value: unknown): value is null | undefined {
  return value === undefined || value === null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function textLines(value: unknown): string[] {
  if (isAbsent(value)) return [];
  return typeof value === 'string' ? [value] : [UNREADABLE];
}

function listLines(value: unknown): string[] {
  if (isAbsent(value)) return [];
  if (!Array.isArray(value)) return [UNREADABLE];
  return value.map((item) => (typeof item === 'string' ? item : UNREADABLE));
}

function stepLines(value: unknown): string[] {
  if (isAbsent(value)) return [];
  if (!Array.isArray(value)) return [UNREADABLE];
  return value.map((step) => {
    if (!isRecord(step)) return UNREADABLE;
    const { title, detail } = step;
    return typeof title === 'string' && typeof detail === 'string'
      ? `${title}: ${detail}`
      : UNREADABLE;
  });
}

function offerTypeLines(value: unknown): string[] {
  return textLines(value).map((line) => (line === UNREADABLE ? line : offerTypeLabel(line)));
}

function postageLines(kind: unknown, amountMinor: unknown, currency: unknown): string[] {
  if (isAbsent(kind)) return [];
  if (typeof kind !== 'string') return [UNREADABLE];
  const label = labelFor(POSTAGE_KIND_LABELS, kind);
  if (kind !== 'COST' || isAbsent(amountMinor) || isAbsent(currency)) return [label];
  if (typeof amountMinor !== 'number' || !Number.isFinite(amountMinor)) return [UNREADABLE];
  if (typeof currency !== 'string') return [UNREADABLE];
  return [`${label}: ${formatMinorUnits(amountMinor, currency)}`];
}

function yesNoLines(value: unknown): string[] {
  if (value === true) return ['Yes'];
  if (value === false) return ['No'];
  return isAbsent(value) ? [] : [UNREADABLE];
}

function dateLines(value: unknown, formatDate: (value: string) => string): string[] {
  if (isAbsent(value) || value === '') return [];
  if (typeof value !== 'string') return [UNREADABLE];
  return [formatDate(value) || value];
}

/** Whether two timestamps are the same moment, however each is spelled (Z, +00:00, -05:00); a null is only a null. */
function sameInstant(current: string | null, proposed: unknown): boolean {
  const left = current === '' ? null : current;
  const right = proposed === '' || isAbsent(proposed) ? null : proposed;
  if (left === null || right === null) return left === right;
  if (typeof right !== 'string') return false;
  const leftTime = Date.parse(left);
  const rightTime = Date.parse(right);
  return Number.isNaN(leftTime) || Number.isNaN(rightTime)
    ? left === right
    : leftTime === rightTime;
}

function sameLines(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((line, index) => line === b[index]);
}

function lines(values: string[]): string[] {
  const kept = values.filter((value) => value.trim() !== '');
  return kept.length > 0 ? kept : [NONE];
}

function row(
  key: string,
  label: string,
  current: string[],
  proposed: string[],
  same?: boolean
): ProposalRow {
  const left = lines(current);
  const right = lines(proposed);
  return {
    key,
    label,
    current: left,
    proposed: right,
    changed: !(same ?? sameLines(left, right)),
    applies: true,
  };
}

function directLinkCount(count: number): string {
  return count === 0 ? 'no direct link' : `${count} direct links`;
}

/**
 * The retailer's link row. Applying only moves a link when the offer has exactly one `direct` link; with none or
 * several it leaves links alone, so the row is never a change and says so in a note instead.
 */
function linkRow(offer: OfferDetailView, proposed: unknown): ProposalRow {
  const direct = offer.links.filter((link) => link.provider === 'direct');
  if (direct.length === 1) {
    return row('link', 'Retailer link', [direct[0].destinationUrl], textLines(proposed));
  }
  if (typeof proposed !== 'string' || proposed === '') {
    return row('link', 'Retailer link', [], textLines(proposed));
  }
  const known = direct.some((link) => link.destinationUrl === proposed);
  return {
    ...row('link', 'Retailer link', [], [proposed], true),
    applies: false,
    note: known
      ? undefined
      : `The retailer's link will not be applied because this offer has ${directLinkCount(direct.length)}. Update the link yourself.`,
  };
}

/**
 * The fields of a retailer's proposal next to what the offer says now, in the order an editor reads an offer. The
 * proposal is the sanitised content the partner API stored, but it is read as untrusted: it is shown as text and
 * nothing here interprets it, and a value of the wrong kind shows as `UNREADABLE` rather than as empty. `categoryName`
 * turns an id into a name for both sides; `formatDate` is the admin date-and-time formatter (a date it cannot read
 * shows as it was sent).
 */
export function buildProposalRows(
  offer: OfferDetailView,
  payload: Payload,
  categoryName: (id: string) => string,
  formatDate: (value: string) => string
): ProposalRow[] {
  const proposedCategory = payload.category_id;
  const categoryLines = (id: unknown): string[] => {
    if (isAbsent(id)) return [];
    return typeof id === 'string' ? [categoryName(id)] : [UNREADABLE];
  };
  const subscription = payload.subscription_terms;
  return [
    row('title', 'Title', textLines(offer.title), textLines(payload.title)),
    row('summary', 'Summary', textLines(offer.summary), textLines(payload.summary)),
    row('description', 'Description', offer.description, listLines(payload.description)),
    row(
      'offer_type',
      'Offer type',
      [offerTypeLabel(offer.offerType)],
      offerTypeLines(payload.offer_type)
    ),
    row(
      'category',
      'Category',
      categoryLines(offer.categoryId),
      categoryLines(proposedCategory),
      proposedCategory === offer.categoryId
    ),
    row(
      'postage',
      'Postage',
      postageLines(offer.postageKind, offer.postageAmountMinor, offer.postageCurrency),
      postageLines(payload.postage_kind, payload.postage_amount_minor, payload.postage_currency)
    ),
    row(
      'requires_card',
      OFFER_FIELD_LABELS.requiresCard,
      yesNoLines(offer.requiresCard),
      yesNoLines(payload.requires_card)
    ),
    row(
      'subscription_terms',
      'Subscription terms',
      offer.subscriptionTerms ? [offer.subscriptionTerms] : [],
      textLines(subscription)
    ),
    row(
      'eligibility',
      OFFER_FIELD_LABELS.eligibility,
      textLines(offer.eligibility),
      textLines(payload.eligibility)
    ),
    row(
      'availability',
      'Availability',
      textLines(offer.availability),
      textLines(payload.availability)
    ),
    row(
      'steps',
      'Steps',
      offer.steps.map((step) => `${step.title}: ${step.detail}`),
      stepLines(payload.steps)
    ),
    row(
      'start_at',
      OFFER_FIELD_LABELS.startAt,
      dateLines(offer.startAt, formatDate),
      dateLines(payload.start_at, formatDate),
      sameInstant(offer.startAt, payload.start_at)
    ),
    row(
      'end_at',
      OFFER_FIELD_LABELS.endAt,
      dateLines(offer.endAt, formatDate),
      dateLines(payload.end_at, formatDate),
      sameInstant(offer.endAt, payload.end_at)
    ),
    linkRow(offer, payload.destination_url),
  ];
}
