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

/** The offer as a retailer sends it. Every key is shown, including the ones that may be null. */
const OFFER_BODY = {
  title: 'Free sample of Example Tea',
  summary: 'A free sachet of Example Tea, posted to you.',
  description: ['Try our best-selling blend before you buy a box.'],
  offer_type: 'FREE_SAMPLE',
  category: 'food-and-drink',
  url: 'https://shop.example/free-sample',
  postage_kind: 'FREE',
  postage_amount_minor: null,
  postage_currency: null,
  requires_card: false,
  subscription_terms: null,
  eligibility: 'UK residents aged 18 or over. One per household.',
  availability: 'While stocks last.',
  steps: [
    { title: 'Open the offer page', detail: 'Choose the Example Tea sample.' },
    { title: 'Enter your address', detail: 'We post the sample within 14 days.' },
  ],
  start_at: null,
  end_at: '2027-06-30T23:59:00Z',
};

/** A submission as the status endpoints show it. */
const SUBMISSION_VIEW = {
  ref: 'example-tea-sample',
  status: 'pending_review',
  title: 'Free sample of Example Tea',
  public_url: null,
  update_pending: false,
  submitted_at: '2026-11-11T09:30:00Z',
  ends_at: '2027-06-30T23:59:00Z',
};

const LIVE_VIEW = {
  ...SUBMISSION_VIEW,
  ref: 'example-coffee-trial',
  status: 'live',
  title: 'Example Coffee trial',
  public_url: 'https://freejoy.example/freebies/example-coffee-trial',
  update_pending: true,
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
  'partner-offers-put': {
    intro:
      'Creates an offer submission, or updates the one with the same ref. Send the whole offer. Nothing you send is ever published by this API: it is stored as a draft for our editors, who decide whether it goes live.',
    notes: [
      '`ref` is your own id for the offer, and the path carries it. Two merchants may use the same ref; yours are only ever yours.',
      'The answer carries an `outcome`. `created` is a new submission (status 201). `refreshed` means our editors had not touched it, so it took your new content. `pending_update` means our editors have edited, published or ended it, so your new content waits for them and the published page has not changed. `unchanged` means you sent what we already hold.',
      'Sending the same body again changes nothing, so a retry after a timeout is safe. The order of the keys does not matter.',
      '`url` must be an https address on your own website. `start_at` can be at most 90 days ahead, and `end_at` must be in the future. Both are date-times with a time zone, such as `2027-06-30T23:59:00Z`.',
      'A `FREE_TRIAL` needs `subscription_terms`. A `COST` postage needs `postage_amount_minor` and `postage_currency`, and no other postage kind may send them.',
      'An offer one of our editors archived is always refused with `offer_closed`, and that is permanent: use a new `ref`.',
    ],
    request: `${CURL}${CONTINUE}--request PUT${CONTINUE}--header "Content-Type: application/json"${CONTINUE}--data @offer.json${CONTINUE}"$FREEJOY_PARTNER_URL/offers/example-tea-sample"`,
    requestBody: json(OFFER_BODY),
    response: json({
      success: true,
      data: { ...SUBMISSION_VIEW, outcome: 'created' },
    }),
    statuses: {
      '200': {
        meaning:
          'The offer already existed. The body is described under the endpoint, and `outcome` says what happened.',
      },
      '201': {
        meaning: 'A new submission was created. It waits for our editors.',
      },
      '409': {
        meaning:
          'One of our editors archived this offer. That is permanent. Submit the offer again under a new `ref`. The code is `offer_closed`.',
      },
      '422': {
        message: 'Invalid request',
        meaning:
          'A value is missing or wrong, or breaks a rule. A rule has a `code` (see the error codes below). A body that is not the documented shape has none.',
      },
      '429': {
        message: 'Too many requests',
        meaning:
          'You went over a limit: a request limit (wait the number of seconds in the `Retry-After` header) or one of the three limits on writes, each with a `code` (see the error codes below).',
      },
    },
  },
  'partner-offers-list': {
    intro:
      'Lists your submissions, newest first, with the status of each, the public page while it is live and whether an update is waiting for our editors.',
    notes: [
      'Use `limit` and `offset` to page through the list. `total_count` is how many you have in all.',
      'The status is `pending_review`, `live`, `ended` or `declined`. It is worked out from what our editors did, so it can change without you sending anything.',
    ],
    request: `${CURL}${CONTINUE}"$FREEJOY_PARTNER_URL/offers?limit=20&offset=0"`,
    response: json({
      success: true,
      data: { offers: [LIVE_VIEW, SUBMISSION_VIEW], total_count: 2, limit: 20, offset: 0 },
    }),
    statuses: {
      '422': {
        message: 'Invalid request',
        meaning: '`limit` or `offset` is not a whole number in its range.',
      },
    },
  },
  'partner-offers-get': {
    intro:
      'Reads one of your submissions: its status, its public page while it is live, and whether an update is waiting for our editors.',
    notes: [
      'A ref that is not yours is answered exactly like one that does not exist.',
      '`update_pending` going back to false does not mean the update was applied. Read the title and the page to see what is published.',
    ],
    request: `${CURL}${CONTINUE}"$FREEJOY_PARTNER_URL/offers/example-coffee-trial"`,
    response: json({ success: true, data: LIVE_VIEW }),
    statuses: {
      '404': {
        message: 'Not Found',
        meaning: 'You have no offer with this ref.',
      },
      '422': {
        message: 'Invalid request',
        meaning: 'The ref is not written the way a ref is (see the `ref` parameter).',
      },
    },
  },
  'partner-offers-end': {
    intro:
      'Ends an offer because the sample has run out. Its end date becomes now, so it stops being offered at once.',
    notes: [
      'Ending cannot be undone by this API. Sending the offer again without a future `end_at` does not reopen it.',
      'An update that was waiting for our editors when you ended the offer is discarded.',
      'Calling it again changes nothing: `changed` is false.',
    ],
    request: `${CURL}${CONTINUE}--request POST${CONTINUE}"$FREEJOY_PARTNER_URL/offers/example-coffee-trial/end"`,
    response: json({
      success: true,
      data: {
        ...LIVE_VIEW,
        status: 'ended',
        public_url: null,
        update_pending: false,
        ends_at: '2026-11-11T10:05:00Z',
        changed: true,
      },
    }),
    statuses: {
      '404': {
        message: 'Not Found',
        meaning: 'You have no offer with this ref.',
      },
      '422': {
        message: 'Invalid request',
        meaning: 'The ref is not written the way a ref is (see the `ref` parameter).',
      },
      '429': {
        message: 'Too many requests',
        meaning:
          'You went over a request limit or your daily limit on writes. Wait the number of seconds in the `Retry-After` header.',
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
    status: '409',
    message: 'Conflict',
    meaning: 'The request clashes with the state of what it names. See the endpoint.',
  },
  {
    status: '413',
    message: 'Payload too large',
    meaning: 'The request body is over 256 KiB (262,144 bytes).',
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
  {
    code: 'invalid_url',
    status: '422',
    meaning: 'The offer `url` is not an https address, or it is not a well-formed address.',
  },
  {
    code: 'link_host_not_allowed',
    status: '422',
    meaning:
      'The offer `url` is not on your own website. Freejoy holds one website for you; send an address on that host (or its `www` form).',
  },
  {
    code: 'unknown_category',
    status: '422',
    meaning: 'The `category` is not one of our categories that is open to submissions.',
  },
  {
    code: 'invalid_postage',
    status: '422',
    meaning:
      '`postage_amount_minor` and `postage_currency` must both be sent when `postage_kind` is `COST`, and neither otherwise.',
  },
  {
    code: 'invalid_window',
    status: '422',
    meaning:
      '`start_at` or `end_at` has no time zone, is not a real date-time, `end_at` is not in the future, `end_at` is not after `start_at`, or `start_at` is more than 90 days ahead.',
  },
  {
    code: 'empty_text',
    status: '422',
    meaning: 'A text field has nothing left once markup and spaces are removed.',
  },
  {
    code: 'subscription_terms_required',
    status: '422',
    meaning: 'A `FREE_TRIAL` needs `subscription_terms`.',
  },
  {
    code: 'offer_closed',
    status: '409',
    meaning:
      'One of our editors archived this offer. That is permanent: submit the offer again under a new `ref`.',
  },
  {
    code: 'open_submission_cap',
    status: '429',
    meaning:
      'You have 50 offers waiting for review. It clears only as our editors review them, not with time, so do not retry on a timer. Updating an existing ref is not refused.',
  },
  {
    code: 'daily_submission_cap',
    status: '429',
    meaning:
      'You have submitted 20 new offers today (UTC day). Try again tomorrow. Updating an existing ref is not refused.',
  },
  {
    code: 'daily_write_budget',
    status: '429',
    meaning:
      'You have used your 2,000 writes for the day (UTC day), across all of your keys. Wait the number of seconds in the `Retry-After` header.',
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
  {
    name: 'Writes',
    value:
      'Creating, updating and ending an offer: 30 requests a minute for each key, and 2,000 a day (UTC) for all of your keys together. Over either you get a 429, and the daily one has the code `daily_write_budget`.',
  },
  {
    name: 'Offers waiting for review',
    value:
      'At most 50, and at most 20 new offers a day (UTC). Updating an offer you already sent is never refused for either.',
  },
  {
    name: 'Request body',
    value: 'At most 256 KiB (262,144 bytes). Only the endpoint that sends an offer takes one.',
  },
];
