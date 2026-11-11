import { clickDefinition } from './schema';

/**
 * The words and examples around the endpoints on `/developers/partner-api`.
 *
 * The endpoint list itself comes from the partner OpenAPI document (`schema.ts`). This module holds only what a
 * schema cannot say, keyed by the operation id. To add an endpoint: regenerate the document, then add one entry to
 * `OPERATION_CONTENT` below. `partner-api-content.test.ts` fails until every operation has an entry and fails when
 * an entry names an operation the document does not have, so the page and the document cannot drift.
 *
 * Examples use obviously fake values. The key is written `fj_production_<key id>_<secret>` and the address and key
 * are shell variables, so no example holds a string that looks like a real key. British English, plain words.
 */

export interface OperationContent {
  /** One or two sentences on what the endpoint is for. */
  intro: string;
  /** Short points a caller should know that the schema does not say. */
  notes: readonly string[];
  /** A request, as a shell command. */
  request: string;
  /** The body of a successful answer, as JSON text. */
  response: string;
}

/** Placeholder for a key, shown in prose. Not a string that matches the real key pattern. */
export const KEY_PLACEHOLDER = 'fj_production_<key id>_<secret>';

const CURL = 'curl --header "Authorization: Bearer $FREEJOY_PARTNER_KEY"';

function json(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

const CLICKS_RESPONSE = {
  success: true,
  data: {
    from_date: '2026-03-01',
    to_date: '2026-03-31',
    offers: [
      {
        title: 'Free sample of Example Tea',
        slug: 'example-tea-sample',
        url: 'https://freejoy.example/freebies/example-tea-sample',
        total_clicks: 7,
        days: [
          { date: '2026-03-04', clicks: 3 },
          { date: '2026-03-05', clicks: 4 },
        ],
      },
      {
        title: 'Example Coffee trial',
        slug: 'example-coffee-trial',
        url: 'https://freejoy.example/freebies/example-coffee-trial',
        total_clicks: 0,
        days: [],
      },
    ],
    offers_truncated: false,
    retention: {
      days: 395,
      earliest_complete_date: '2025-02-20',
      starts_before_retention: false,
    },
  },
};

/** One entry per operation id in the partner OpenAPI document. */
export const OPERATION_CONTENT: Readonly<Record<string, OperationContent>> = {
  'partner-me': {
    intro:
      'Returns the name of the merchant your key belongs to and the label the key was given. It changes nothing, so it is a safe first call to check that your key and the address work.',
    notes: [
      'The merchant name is the one Freejoy holds for you. The label is the name Freejoy gave the key, for example the system that uses it.',
    ],
    request: `${CURL} \\n  "$FREEJOY_PARTNER_URL/me"`,
    response: json({
      success: true,
      data: { merchant_name: 'Example Shop', key_label: 'Production feed' },
    }),
  },
  'partner-clicks': {
    intro:
      'Counts the clicks on your published and ended offers, as one count per offer per UTC day. Use it to see what Freejoy sent to your site.',
    notes: [
      'You always get your own offers. There is no merchant or offer parameter, and the key decides whose counts you read.',
      'Offers are listed busiest first. An offer with no clicks in the range is listed with an empty days list. A draft or archived offer is never listed.',
      'Only days with at least one click have a row. A day is a UTC day, and today is not complete until it ends.',
      'When starts_before_retention is true, the days before earliest_complete_date can be missing clicks, because old click records are deleted.',
    ],
    request: `${CURL} \\n  "$FREEJOY_PARTNER_URL/clicks?from=2026-03-01&to=2026-03-31"`,
    response: json({
      ...CLICKS_RESPONSE,
      data: { ...CLICKS_RESPONSE.data, definition: clickDefinition() },
    }),
  },
};

export interface StatusRow {
  status: string;
  message: string;
  meaning: string;
}

/** Every error body is `{"success": false, "error": "<message>"}`, and the message is always one of these. */
export const STATUS_ROWS: readonly StatusRow[] = [
  { status: '200', message: '', meaning: 'It worked. The body is described under the endpoint.' },
  {
    status: '401',
    message: 'Unauthorized',
    meaning:
      'The key is missing, malformed, unknown, revoked or for another environment, or the request also sent cookies or another credential. Every one of these gets the same answer.',
  },
  {
    status: '404',
    message: 'Not Found',
    meaning: 'No endpoint has that path.',
  },
  {
    status: '405',
    message: 'Method Not Allowed',
    meaning:
      'The path exists but not for that method. The Allow header lists the methods it takes.',
  },
  {
    status: '413',
    message: 'Payload too large',
    meaning: 'The request body is over 256 KiB. No endpoint takes a body yet.',
  },
  {
    status: '422',
    message: 'Invalid request',
    meaning:
      'A parameter is missing or wrong. A refused date range has its own message and the code invalid_range.',
  },
  {
    status: '429',
    message: 'Too many requests',
    meaning:
      'You went over a limit. Wait the number of seconds in the Retry-After header. Repeated wrong keys for one key id also get this.',
  },
  {
    status: '500',
    message: 'Internal server error',
    meaning: 'Something went wrong on our side. Nothing about your request is repeated back.',
  },
  {
    status: '503',
    message: 'Service temporarily unavailable',
    meaning:
      'A service Freejoy depends on is not available. Try again after the Retry-After header.',
  },
];

export interface ErrorCodeRow {
  code: string;
  status: string;
  meaning: string;
}

/** A body may also carry a stable `code`. Match on the code, never on the message. */
export const ERROR_CODE_ROWS: readonly ErrorCodeRow[] = [
  {
    code: 'invalid_range',
    status: '422',
    meaning:
      'from or to is missing, is not a real date written YYYY-MM-DD, is after the other, is more than 92 days apart or is outside the years 2000 to 2999. The message states the rule and repeats nothing you sent.',
  },
];

export interface LimitRow {
  name: string;
  value: string;
}

/** Checked against the backend sources by `partner-api-content.test.ts`. */
export const LIMIT_ROWS: readonly LimitRow[] = [
  { name: 'Reads', value: '120 requests a minute for each key. Over that you get a 429.' },
  { name: 'Clicks date range', value: 'At most 92 days, counting both ends, from 2000 to 2999.' },
  {
    name: 'Clicks offers returned',
    value: 'At most 100, busiest first. offers_truncated says when there were more.',
  },
  { name: 'Click records kept', value: '395 days (13 months). Older days can be incomplete.' },
  { name: 'Request body', value: 'At most 256 KiB. No endpoint takes one yet.' },
];
