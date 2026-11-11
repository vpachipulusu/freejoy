import { offerTypeLabel, POSTAGE_KIND_LABELS } from './offer-labels';
import type { OfferDetailView } from './offer-view';

/** One field of the comparison an editor reads before applying a retailer's update. */
export interface ProposalRow {
  key: string;
  label: string;
  /** Lines of text: a one-line field has one line, a list has one per item. Never empty (see `NONE`). */
  current: string[];
  proposed: string[];
  changed: boolean;
}

export const NONE = 'None';
const LINK_NOT_COMPARABLE = 'Not changed here (this offer has no single retailer link)';

type Payload = Record<string, unknown>;

function text(payload: Payload, key: string): string {
  const value = payload[key];
  return typeof value === 'string' ? value : '';
}

function lines(values: string[]): string[] {
  const kept = values.filter((value) => value.trim() !== '');
  return kept.length > 0 ? kept : [NONE];
}

function textList(payload: Payload, key: string): string[] {
  const value = payload[key];
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === 'string')
    : [];
}

function stepLines(payload: Payload): string[] {
  const value = payload.steps;
  if (!Array.isArray(value)) return [];
  return value.map((step) => {
    const record = (step ?? {}) as Payload;
    return `${text(record, 'title')}: ${text(record, 'detail')}`;
  });
}

function postageText(kind: string, amountMinor: unknown, currency: unknown): string {
  const label = POSTAGE_KIND_LABELS[kind] ?? kind;
  if (kind !== 'COST' || typeof amountMinor !== 'number' || typeof currency !== 'string') {
    return label;
  }
  return `${label}: ${currency} ${(amountMinor / 100).toFixed(2)}`;
}

function yesNo(value: unknown): string {
  return value === true ? 'Yes' : 'No';
}

function sameLines(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((line, index) => line === b[index]);
}

function row(key: string, label: string, current: string[], proposed: string[]): ProposalRow {
  const left = lines(current);
  const right = lines(proposed);
  return { key, label, current: left, proposed: right, changed: !sameLines(left, right) };
}

/**
 * The fields of a retailer's proposal next to what the offer says now, in the order an editor reads an offer. The
 * proposal is the sanitised content the partner API stored (plain text, already validated), so it is shown as
 * text and nothing here interprets it. `categoryName` turns an id into a name for both sides; `formatDate` is the
 * admin date formatter (a date it cannot read shows as it was sent).
 */
export function buildProposalRows(
  offer: OfferDetailView,
  payload: Payload,
  categoryName: (id: string) => string,
  formatDate: (value: string) => string
): ProposalRow[] {
  const date = (value: string | null | undefined): string[] =>
    value ? [formatDate(value) || value] : [];
  const direct = offer.links.filter((link) => link.provider === 'direct');
  const currentLink = direct.length === 1 ? direct[0].destinationUrl : LINK_NOT_COMPARABLE;
  const proposedLink = text(payload, 'destination_url');
  const kind = text(payload, 'postage_kind');
  return [
    row('title', 'Title', [offer.title], [text(payload, 'title')]),
    row('summary', 'Summary', [offer.summary], [text(payload, 'summary')]),
    row('description', 'Description', offer.description, textList(payload, 'description')),
    row(
      'offer_type',
      'Offer type',
      [offerTypeLabel(offer.offerType)],
      [offerTypeLabel(text(payload, 'offer_type'))]
    ),
    row(
      'category',
      'Category',
      [categoryName(offer.categoryId)],
      [categoryName(text(payload, 'category_id'))]
    ),
    row(
      'postage',
      'Postage',
      [postageText(offer.postageKind, offer.postageAmountMinor, offer.postageCurrency)],
      [postageText(kind, payload.postage_amount_minor, payload.postage_currency)]
    ),
    row(
      'requires_card',
      'Card required',
      [yesNo(offer.requiresCard)],
      [yesNo(payload.requires_card)]
    ),
    row(
      'subscription_terms',
      'Subscription terms',
      offer.subscriptionTerms ? [offer.subscriptionTerms] : [],
      text(payload, 'subscription_terms') ? [text(payload, 'subscription_terms')] : []
    ),
    row('eligibility', 'Eligibility', [offer.eligibility], [text(payload, 'eligibility')]),
    row('availability', 'Availability', [offer.availability], [text(payload, 'availability')]),
    row(
      'steps',
      'Steps',
      offer.steps.map((step) => `${step.title}: ${step.detail}`),
      stepLines(payload)
    ),
    row('start_at', 'Starts', date(offer.startAt), date(text(payload, 'start_at') || null)),
    row('end_at', 'Ends', date(offer.endAt), date(text(payload, 'end_at') || null)),
    row('link', 'Retailer link', [currentLink], [proposedLink]),
  ];
}
