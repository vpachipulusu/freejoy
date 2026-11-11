import { clickDefinition } from './schema';

/**
 * The words and examples around the endpoints on `/developers/partner-api`.
 *
 * The endpoint list itself comes from the partner OpenAPI document (`schema.ts`). This module holds only what a
 * schema cannot say, keyed by the operation id. To add an endpoint: regenerate the document, then add one entry to
 * `OPERATION_CONTENT` below (with a request body example if the operation has a body, and a `statuses` entry for any
 * status whose meaning is specific to it). `partner-api-content.test.ts` fails until every operation has an entry and
 * fails when an entry names an operation the document does not have, so the page and the document cannot drift.
 *
 * Text between backticks is shown as code (see `rich-text.tsx`): use it for field, parameter and code names.
 *
 * Examples use obviously fake values. The key is written `fj_production_<key id>_<secret>` and the address and key
 * are shell variables, so no example holds a string that looks like a real key. British English, plain words.
 */

export interface StatusText {
  /** The fixed message in the body. Defaults to the global message for the status. */
  message?: string;
  meaning: string;
}

export interface OperationContent {
  /** One or two sentences on what the endpoint is for. */
  intro: string;
  /** Short points a caller should know that the schema does not say. */
  notes: readonly string[];
  /** A request, as a shell command. A real backslash and line break continue a line; never the two characters `\` `n`. */
  request: string;
  /** The JSON body of the request, when the operation has one. */
  requestBody?: string;
  /** The body of a successful answer, as JSON text. */
  response: string;
  /** What a status means for this operation, where that differs from the general meaning below. */
  statuses?: Readonly<Record<string, StatusText>>;
}

/** Placeholder for a key, shown in prose. Not a string that matches the real key pattern. */
export const KEY_PLACEHOLDER = 'fj_production_<key id>_<secret>';

/** What `$FREEJOY_PARTNER_URL` is, with a reserved `.example` host: the site origin followed by `/partner/v1`. */
export const PARTNER_BASE_EXAMPLE = 'https://www.freejoy.example/partner/v1';

const CURL = 'curl --header "Authorization: Bearer $FREEJOY_PARTNER_KEY"';
/** A backslash and a real line break: the way a shell command continues on the next line. */
const CONTINUE = ' \\\n  ';

function json(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

const CLICKS_DATA = {
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
};

/** One entry per operation id in the partner OpenAPI document. */
export const OPERATION_CONTENT: Readonly<Record<string, OperationContent>> = {
  'partner-me': {
    intro:
      'Returns the name of the merchant your key belongs to and the label the key was given. It changes nothing, so it is a safe first call to check that your key and the address work.',
    notes: [
      'The merchant name is the one Freejoy holds for you. The label is the name Freejoy gave the key, for example the system that uses it.',
    ],
    request: `${CURL}${CONTINUE}"$FREEJOY_PARTNER_URL/me"`,
    response: json({
      success: true,
      data: { merchant_name: 'Example Shop', key_label: 'Production feed' },
    }),
    statuses: {
      '422': {
        meaning:
          'The request was not valid. This endpoint takes no parameters, so you should not see this.',
      },
    },
  },
  'partner-clicks': {
    intro:
      'Counts the clicks on your published and ended offers, as one count per offer per UTC day. Use it to see what Freejoy sent to your site.',
    notes: [
      'You always get your own offers. There is no merchant or offer parameter, and the key decides whose counts you read.',
      'Offers are listed busiest first. An offer with no clicks in the range is listed with an empty `days` list. A draft or archived offer is never listed.',
      'Only days with at least one click have a row. A day is a UTC day, and today is not complete until it ends.',
      'When `starts_before_retention` is true, the days before `earliest_complete_date` can be missing clicks, because old click records are deleted.',
      'Each of `from` and `to` must appear once. Repeating one is refused.',
    ],
    request: `${CURL}${CONTINUE}"$FREEJOY_PARTNER_URL/clicks?from=2026-03-01&to=2026-03-31"`,
    response: json({
      success: true,
      data: { ...CLICKS_DATA, definition: clickDefinition() },
    }),
    statuses: {
      '422': {
        message: 'The date range message',
        meaning:
          'Anything wrong with `from` or `to` is refused with one message and the code `invalid_range`. See the error codes below.',
      },
      '429': {
        meaning:
          'You went over the general read limit or the lower limit for this endpoint. Wait the number of seconds in the `Retry-After` header.',
      },
    },
  },
};

export interface StatusRow {
  status: string;
  message: string;
  meaning: string;
}

/** The general meaning of each status. An operation can say something more specific in its own `statuses`. */
export const STATUS_ROWS: readonly StatusRow[] = [
  { status: '200', message: '', meaning: 'It worked. The body is described under the endpoint.' },
  {
    status: '201',
    message: '',
    meaning: 'It worked and something was created. The body is described under the endpoint.',
  },
  { status: '204', message: '', meaning: 'It worked. There is no body.' },
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
      'The path exists but not for that method. The `Allow` header lists the methods it takes.',
  },
  {
    status: '413',
    message: 'Payload too large',
    meaning: 'The request body is over 256 KiB (262,144 bytes). No endpoint takes a body.',
  },
  {
    status: '422',
    message: 'Invalid request',
    meaning: 'A parameter is missing or wrong.',
  },
  {
    status: '429',
    message: 'Too many requests',
    meaning:
      'You went over a limit. Wait the number of seconds in the `Retry-After` header. Repeated wrong keys for one key id also get this.',
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
      'Freejoy could not answer, for example because a service it depends on is not available. Try again after the number of seconds in the `Retry-After` header.',
  },
];

export interface ResolvedStatus {
  message: string;
  meaning: string;
}

/** What a status means for an operation: its own text first, then the general row. An unknown status stops the build. */
export function describeStatus(
  operationId: string,
  status: string,
  content: OperationContent | undefined = OPERATION_CONTENT[operationId]
): ResolvedStatus {
  const general = STATUS_ROWS.find((row) => row.status === status);
  const own = content?.statuses?.[status];
  if (!general && !own) {
    throw new Error(
      `Status ${status} of ${operationId} has no row in STATUS_ROWS and no entry in its \`statuses\` (lib/partner-api/content.ts).`
    );
  }
  return {
    message: own?.message ?? general?.message ?? '',
    meaning: own?.meaning ?? general?.meaning ?? '',
  };
}

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
      '`from` or `to` is missing or given more than once, is not a real date written YYYY-MM-DD, `from` is after `to`, the range covers more than 92 days (counting both ends) or it falls outside the years 2000 to 2999. The message states the rule and repeats nothing you sent.',
  },
];

export interface LimitRow {
  name: string;
  value: string;
}

/** Checked against the backend sources by `partner-api-content.test.ts`. */
export const LIMIT_ROWS: readonly LimitRow[] = [
  { name: 'Reads', value: '120 requests a minute for each key. Over that you get a 429.' },
  {
    name: 'Clicks reads',
    value:
      '20 requests a minute for each key, counted as well as the 120 above. Over that you get a 429.',
  },
  {
    name: 'Clicks date range',
    value: 'At most 92 days, counting both ends, between the years 2000 and 2999.',
  },
  {
    name: 'Clicks offers returned',
    value: 'At most 100, busiest first. `offers_truncated` says when there were more.',
  },
  { name: 'Click records kept', value: '395 days (13 months). Older days can be incomplete.' },
  { name: 'Request body', value: 'At most 256 KiB (262,144 bytes). No endpoint takes one.' },
];
