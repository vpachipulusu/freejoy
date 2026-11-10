/**
 * Keeps one-use alert tokens, email addresses and cookies out of Sentry (FREEJ-24, FREEJ-31).
 *
 * No page puts an email address in its URL (the newsletter band is a plain link and `/alerts` ignores `?email=`),
 * but the privacy notice must stay true if one ever does, and the emailed confirm and unsubscribe links carry
 * `?token=...`. So page URLs, request query strings, transaction and span names, span attributes, breadcrumb
 * `to`/`from`/`url`, and the `referer` header are all scrubbed of:
 *
 * - a `token` parameter (exact name);
 * - any parameter whose name contains `email` or `e-mail` (`email`, `Email`, `newsletter_email`, `emailAddress`,
 *   `EMAIL%5B%5D`), in the query or in a `#fragment`;
 * - any other parameter whose value is an email address (`?q=a%40b.com`: the public search box and the admin user
 *   search both take free text, and people type addresses into them).
 *
 * Last, any address-shaped token in any string (`name@domain.tld`, plain or percent-encoded, also twice) is
 * replaced wherever it sits: a path segment (`/contacts/a%40b.com`), userinfo (`https://a%40b.com@host/`), a
 * nameless query (`?a%40b.com`), a fragment or free text; letters of any script count, also percent-encoded
 * (`victim@b%C3%BCcher.example`). The domain must be dotted and end in a label that starts with a letter, so
 * `user@host`, `@scope/pkg`, `@Injectable()` and `pkg@8.1.0` are left alone. That rule is lossy in the other
 * direction too: `logo@2x.png`, `icon@3x.webp`, `app@latest.js` and `version@1.0.beta` become `[Filtered]`
 * (over-filtering, not a leak). Not covered (the part of the address before the stop leaks):
 * - a local part with a character outside letters, digits and `_.+!%-` (`o'brien@x.test`, `a#1@x.test`,
 *   `a&b@x.test`, `a=b@x.test`, a quoted part with a space);
 * - a local part longer than 256 characters (its last 256 are replaced);
 * - other spellings of `@`: full-width, `&#64;`, a literal `@`;
 * - an IP-literal domain (`victim@192.168.0.1`);
 * - percent-encoded letters in a parameter name (`?%65mail=`, `?t%6Fken=`; bracket escapes such as `%5B` work);
 * - the second half of a surrogate pair at the start of a local part.
 *
 * Each parameter shape is covered in plain, percent-encoded (also nested in another URL, and encoded twice),
 * JSON, escaped-JSON and percent-encoded-JSON form, plus query strings held as an object or as name/value
 * pairs. Next.js also sends the route it is rendering in the `next-router-state-tree` header, which can embed
 * the query as JSON (`__PAGE__?{"token":"..."}`); that header is replaced whole.
 * `scrubEvent` is used as `beforeSend` and `beforeSendTransaction` in the client, server and edge configs.
 *
 * Pure: it never mutates its input, walks only plain objects and arrays, and returns every other value (dates,
 * class instances) as it is. It never throws: an event it cannot walk safely is dropped (`null`, which Sentry
 * treats as "do not send"; the SDK records a dropped `before_send` event in its Client Reports, so keep
 * `sendClientReports` on). A cycle becomes `[Circular]`, a child shared by several parents is walked once, an
 * object nested deeper than `MAX_DEPTH` becomes `[Filtered]`, and more than `MAX_NODES` objects drops the event.
 * The memo ignores depth and ancestry, so a reused child can only be over-filtered, never leaked.
 * Sentry's own `normalizeDepth` (default 3) already truncates and de-cycles most events before the hooks run
 * and is the first line of defence; these limits cover whatever still gets through.
 */

export const FILTERED = '[Filtered]';

const MAX_DEPTH = 12;
/** Objects and arrays visited per event; past this the event is dropped. */
const MAX_NODES = 100_000;
const CIRCULAR = '[Circular]';
const QUERY_KEYS = new Set(['query_string', 'query', 'http.query', 'url.query']);
/** Header whose value carries the rendered route, including its query. Matched in lower case. */
const ROUTER_STATE_KEYS = new Set(['next-router-state-tree']);
/** Cookies are never sent. Matched in lower case. */
const COOKIE_KEYS = new Set(['cookie', 'cookies', 'set-cookie']);
/**
 * Span and trace attributes that hold a request body. The Saved page posts the visitor's list of saved offer
 * slugs, so no request body is ever sent (FREEJ-28); the event's own `request.data` is dropped the same way.
 */
const BODY_ATTRIBUTE_KEYS = new Set(['http.request.body.data']);
/** Sentry's internal bookkeeping (removed before sending): kept by reference, never walked. */
const PASS_THROUGH_KEY = 'sdkProcessingMetadata';

// `%` followed by any number of `25` is one percent sign encoded zero or more times (`%3D`, `%253D`, ...).
const PCT = '%(?:25)*';
/** One character of a parameter name: a word character, `.`, `[`, `]`, `-`, or a percent-escape that is no delimiter. */
const NAME_CHAR = `(?:[\\w.[\\]-]|%(?!(?:25)*(?:3D|26|3F|23))[0-9a-f]{2})`;
/** A parameter name that is always filtered: `token`, or anything containing `email` / `e-mail`. */
// The name is asserted once in a lookahead and then consumed by one unambiguous `NAME_CHAR+`, so a long run of
// look-alike text (`emailemailemail...`) costs one scan per start, not a quadratic number of retries.
const SENSITIVE_NAME = `(?=(?:token(?!${NAME_CHAR})|${NAME_CHAR}*e-?mail))${NAME_CHAR}+`;
const ANY_NAME = `${NAME_CHAR}+`;
/** What can start a parameter: the start, `?`, `&`, `;`, `#`, or one of those percent-encoded (a URL in a URL). */
const START = `(^|[?&;#]|${PCT}(?:3F|26|23))`;
const EQUALS = `(?:=|${PCT}3D)`;
/** A value runs up to `&`, `#`, `;`, white space, a quote, or an encoded `&` or `#`. */
const VALUE = `(?:(?!${PCT}(?:26|23))[^&#;\\s"'])*`;
const ADDRESS_MARK = /@|%(?:25)*40/i;

const NAMED_PARAM = new RegExp(`${START}(${SENSITIVE_NAME}${EQUALS})${VALUE}`, 'gi');
const ANY_PARAM = new RegExp(`${START}(${ANY_NAME}${EQUALS})(${VALUE})`, 'gi');
const JSON_NAME = '(?=(?:token(?![\\w.-])|[\\w.-]*e-?mail))[\\w.-]+';

// Each pattern keeps its prefix and suffix and replaces only the value.
const JSON_PATTERNS: ReadonlyArray<readonly [RegExp, string]> = [
  // {"email":"abc"}
  [new RegExp(`("${JSON_NAME}"\\s*:\\s*")[^"]*(")`, 'gi'), `$1${FILTERED}$2`],
  // {\"email\":\"abc\"} (JSON inside a JSON string)
  [
    new RegExp(`(\\\\"${JSON_NAME}\\\\"\\s*:\\s*\\\\")(?:(?!\\\\").)*(\\\\")`, 'gi'),
    `$1${FILTERED}$2`,
  ],
  // %22email%22%3A%22abc%22 (URL-encoded JSON, also encoded twice)
  [
    new RegExp(
      `(${PCT}22${JSON_NAME}${PCT}22${PCT}3A(?:${PCT}20)*${PCT}22)(?:(?!${PCT}22).)*(${PCT}22)`,
      'gi'
    ),
    `$1${FILTERED}$2`,
  ],
];

// Address-shaped tokens are found from the `@` outward, so a long run of look-alike characters costs no more
// than the run itself: no pattern has to start at every character.
const MARK_SCAN = /@|%(?:25)*40/gi;
const LOCAL_CHAR = /[\p{L}\p{N}_.+!%-]/u;
const MAX_LOCAL_LENGTH = 256;
const DOT = `(?:\\.|%(?:25)*2E)`;
// A domain label is a letter, digit, `_` or `-` (any script), or a percent-escape that is not a delimiter or `@`, so
// `b%C3%BCcher.example` and `exam%70le.com` count; `%2E` stays the dot.
const LABEL_CHAR = `(?:[\\p{L}\\p{N}_-]|%(?!(?:25)*(?:2E|26|23|3F|3D|2F|3B|40))[0-9a-f]{2})`;
const DOMAIN = new RegExp(
  `${LABEL_CHAR}+(?:${DOT}${LABEL_CHAR}+)*${DOT}\\p{L}${LABEL_CHAR}*`,
  'iuy'
);

function isSensitiveName(name: string): boolean {
  const lower = name.toLowerCase();
  return lower === 'token' || /e-?mail/.test(lower);
}

function hasAddressMark(text: string): boolean {
  return ADDRESS_MARK.test(text);
}

/** The [start, end) spans of address-shaped tokens in `text`, merged where they touch. */
function addressSpans(text: string): Array<[number, number]> {
  const spans: Array<[number, number]> = [];
  MARK_SCAN.lastIndex = 0;
  for (let mark = MARK_SCAN.exec(text); mark !== null; mark = MARK_SCAN.exec(text)) {
    const afterMark = mark.index + mark[0].length;
    DOMAIN.lastIndex = afterMark;
    const domain = DOMAIN.exec(text);
    if (domain === null) continue;
    let start = mark.index;
    while (
      start > 0 &&
      mark.index - start < MAX_LOCAL_LENGTH &&
      LOCAL_CHAR.test(text.charAt(start - 1))
    ) {
      start -= 1;
    }
    if (start === mark.index) continue;
    const end = afterMark + domain[0].length;
    const last = spans[spans.length - 1];
    if (last !== undefined && start <= last[1]) last[1] = Math.max(last[1], end);
    else spans.push([start, end]);
  }
  return spans;
}

function redactAddresses(text: string): string {
  if (!hasAddressMark(text)) return text;
  let result = '';
  let cursor = 0;
  for (const [start, end] of addressSpans(text)) {
    result += text.slice(cursor, start) + FILTERED;
    cursor = end;
  }
  return result + text.slice(cursor);
}

/**
 * The backend's offer read, `/v1/catalogue/offers/<slug>`: the slug says which freebie a visitor opened or
 * saved (the Saved page reads each saved offer), so span names and URLs keep `[slug]` instead. The list read,
 * `/v1/catalogue/offers?...`, has no slug and is left alone.
 */
const OFFER_READ_SLUG = /(\/v1\/catalogue\/offers\/)[^\s/?#&"'\\]+/g;

function scrubString(value: string): string {
  const named = value.replace(NAMED_PARAM, `$1$2${FILTERED}`);
  const valued = named.replace(ANY_PARAM, (match, start: string, name: string, text: string) =>
    hasAddressMark(text) ? `${start}${name}${FILTERED}` : match
  );
  const withoutParams = JSON_PATTERNS.reduce(
    (text, [pattern, replacement]) => text.replace(pattern, replacement),
    valued
  );
  return redactAddresses(withoutParams).replace(OFFER_READ_SLUG, '$1[slug]');
}

/** A string known to be (part of) a query: anything still holding an address after scrubbing goes whole. */
function scrubQueryText(value: string): string {
  const text = scrubString(value);
  return hasAddressMark(text) ? FILTERED : text;
}

function isPlainObject(value: object): value is Record<string, unknown> {
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

/** State shared by one walk: the path from the root, finished children, and the number of nodes seen. */
type Walk = {
  readonly ancestors: Set<object>;
  /** Finished results, one map for query context and one for the rest: a shared child is scrubbed once. */
  readonly done: readonly [Map<object, unknown>, Map<object, unknown>];
  nodes: number;
};

function scrubPair(name: string, value: unknown, depth: number, walk: Walk): unknown[] {
  if (isSensitiveName(name)) return [name, FILTERED];
  const scrubbedValue =
    typeof value === 'string' ? scrubQueryText(value) : scrub(value, depth + 1, true, walk);
  return [scrubQueryText(name), scrubbedValue];
}

/** The event's `request` without its `data` (the body). Anything that is not a plain object passes unchanged. */
function withoutRequestBody(request: unknown): unknown {
  if (typeof request !== 'object' || request === null || !isPlainObject(request)) return request;
  const { data: _body, ...rest } = request;
  return rest;
}

function scrubContainer(value: object, depth: number, inQuery: boolean, walk: Walk): unknown {
  if (Array.isArray(value)) {
    // A query string as [name, value] pairs.
    if (inQuery && value.length === 2 && typeof value[0] === 'string') {
      return scrubPair(value[0], value[1], depth, walk);
    }
    return value.map((item) => scrub(item, depth + 1, inQuery, walk));
  }

  const result: Record<string, unknown> = {};
  for (const [key, item] of Object.entries(value)) {
    const lower = key.toLowerCase();
    if (COOKIE_KEYS.has(lower) || BODY_ATTRIBUTE_KEYS.has(lower)) continue;
    if (depth === 0 && key === PASS_THROUGH_KEY) {
      result[key] = item;
    } else if (ROUTER_STATE_KEYS.has(lower) || (inQuery && isSensitiveName(key))) {
      result[key] = FILTERED;
    } else {
      const child = depth === 0 && key === 'request' ? withoutRequestBody(item) : item;
      result[scrubString(key)] = scrub(child, depth + 1, inQuery || QUERY_KEYS.has(key), walk);
    }
  }
  return result;
}

function scrub(value: unknown, depth: number, inQuery: boolean, walk: Walk): unknown {
  if (typeof value === 'string') return inQuery ? scrubQueryText(value) : scrubString(value);
  if (value === null || typeof value !== 'object') return value;
  if (!Array.isArray(value) && !isPlainObject(value)) return value;
  if (depth >= MAX_DEPTH) return FILTERED;
  if (walk.ancestors.has(value)) return CIRCULAR;

  const finished = walk.done[inQuery ? 1 : 0];
  if (finished.has(value)) return finished.get(value);

  walk.nodes += 1;
  if (walk.nodes > MAX_NODES) throw new Error('event too large to scrub');

  walk.ancestors.add(value);
  try {
    const result = scrubContainer(value, depth, inQuery, walk);
    finished.set(value, result);
    return result;
  } finally {
    walk.ancestors.delete(value);
  }
}

/**
 * A copy of the event with tokens, email addresses in URLs and strings, the router state header and cookies
 * removed, or `null` (drop the event) if it could not be walked safely.
 */
export function scrubEvent<T extends object>(event: T): T | null {
  try {
    const walk: Walk = { ancestors: new Set(), done: [new Map(), new Map()], nodes: 0 };
    return scrub(event, 0, false, walk) as T;
  } catch {
    return null;
  }
}
