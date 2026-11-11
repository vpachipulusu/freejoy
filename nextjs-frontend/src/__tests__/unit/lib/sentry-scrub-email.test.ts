import { describe, expect, it } from 'vitest';
import { FILTERED, scrubEvent } from '@/lib/sentry/scrub-event';
import { expectLinear } from '@/__tests__/unit/helpers/linear-growth';

const ADDRESS = 'victim@example.com';
const ENCODED = 'victim%40example.com';
const DOUBLE_ENCODED = 'victim%2540example.com';
const LEAKS = ['victim', 'example.com', '%40', '%2540', 'abc'];

function expectNoLeak(value: unknown) {
  const text = JSON.stringify(value);
  for (const leak of LEAKS) expect(text).not.toContain(leak);
}

const HOSTILE_URLS = [
  `https://x.test/alerts?email=${ENCODED}&token=abc#frag`,
  `https://x.test/alerts?email=${ADDRESS}&token=abc`,
  `https://x.test/alerts?Email=${ENCODED}&TOKEN=abc`,
  `https://x.test/alerts?EMAIL=${ENCODED}&email=${ENCODED}&token=abc`,
  `https://x.test/alerts?x=1&email=&email=${ENCODED}&token=abc`,
  `https://x.test/alerts?newsletter_email=${ENCODED}&emailAddress=${ENCODED}&token=abc`,
  `https://x.test/alerts?e-mail=${ENCODED}&token=abc`,
  `https://x.test/alerts;email=${ENCODED};token=abc`,
  `https://x.test/alerts?email=${DOUBLE_ENCODED}&token=abc`,
  `https://x.test/search?q=${ENCODED}&token=abc`,
  `https://x.test/admin/users?q=${ADDRESS}&page=2&token=abc`,
  `https://x.test/go?next=%2Falerts%3Femail%3D${ENCODED}%26token%3Dabc`,
  `https://x.test/go?next=%252Falerts%253Femail%253D${DOUBLE_ENCODED}%2526token%253Dabc`,
  `https://x.test/go?next=${encodeURIComponent(`/alerts?email=${ENCODED}&token=abc`)}`,
];

describe('scrubEvent: email addresses in URLs', () => {
  it.each(HOSTILE_URLS)('removes the address and token from %s', (url) => {
    const scrubbed = scrubEvent({
      message: url,
      request: {
        url,
        query_string: url.split('?')[1] ?? '',
        headers: { referer: url, Referrer: url, Accept: '*/*' },
      },
      transaction: `GET ${url}`,
      contexts: { trace: { data: { 'http.url': url, 'url.full': url } } },
      spans: [{ description: `GET ${url}`, data: { 'http.url': url }, name: `GET ${url}` }],
      breadcrumbs: [
        { category: 'navigation', data: { from: url, to: url } },
        { category: 'fetch', data: { url } },
      ],
    });

    expectNoLeak(scrubbed);
    expect(scrubbed?.request.headers.Accept).toBe('*/*');
  });

  it('keeps the path and the harmless parameters, and only filters the values', () => {
    const url = `https://x.test/alerts?utm=a&email=${ENCODED}&plan=free#frag`;

    const scrubbed = scrubEvent({ request: { url } });

    expect(scrubbed?.request.url).toBe(
      `https://x.test/alerts?utm=a&email=${FILTERED}&plan=free#frag`
    );
  });

  it('filters a transaction name and span name written as METHOD /path?email=', () => {
    const scrubbed = scrubEvent({
      transaction: `GET /alerts?email=${ENCODED}`,
      spans: [{ description: `GET /alerts?email=${ENCODED}`, op: 'http.client' }],
    });

    expectNoLeak(scrubbed);
    expect(scrubbed?.transaction).toBe(`GET /alerts?email=${FILTERED}`);
    expect(scrubbed?.spans[0].op).toBe('http.client');
  });

  it('filters the address from navigation breadcrumb to and from', () => {
    const scrubbed = scrubEvent({
      breadcrumbs: [
        {
          category: 'navigation',
          data: { from: `/alerts?email=${ENCODED}`, to: `/alerts/confirm?email=${ENCODED}` },
        },
      ],
    });

    expectNoLeak(scrubbed);
    expect(scrubbed?.breadcrumbs[0].data.to).toBe(`/alerts/confirm?email=${FILTERED}`);
  });

  it('filters span attributes in the plain and the typed form', () => {
    const scrubbed = scrubEvent({
      spans: [
        {
          data: { 'http.query': `?email=${ENCODED}`, 'url.query': `email=${ENCODED}` },
          attributes: {
            'url.full': { type: 'string', value: `https://x.test/a?email=${ENCODED}` },
            'http.url': `https://x.test/a?email=${ENCODED}`,
          },
        },
      ],
    });

    expectNoLeak(scrubbed);
  });

  it.each([
    ['an object', { email: ADDRESS, x: '1' }],
    ['an object with mixed case', { Email: ADDRESS, x: '1' }],
    ['an object with a list', { email: [ADDRESS, ADDRESS], x: '1' }],
    ['an object holding an address under another name', { q: ADDRESS, x: '1' }],
    ['an object holding an encoded address', { q: ENCODED, x: '1' }],
    [
      'a list of pairs',
      [
        ['email', ADDRESS],
        ['x', '1'],
      ],
    ],
    [
      'a list of pairs under another name',
      [
        ['q', ADDRESS],
        ['x', '1'],
      ],
    ],
  ])('filters a query string held as %s', (_name, query) => {
    const scrubbed = scrubEvent({ request: { query_string: query }, extra: { query } });

    expectNoLeak(scrubbed);
  });

  it('keeps the harmless parts of a query held as an object', () => {
    const scrubbed = scrubEvent({ request: { query_string: { email: ADDRESS, x: '1' } } });

    expect(scrubbed?.request.query_string).toEqual({ email: FILTERED, x: '1' });
  });

  it.each([
    ['JSON', `{"email":"${ADDRESS}","x":1}`],
    ['JSON with spaces', `{ "email" : "${ADDRESS}" }`],
    ['JSON with a camel case name', `{"emailAddress":"${ADDRESS}"}`],
    ['escaped JSON', `{\\"email\\":\\"${ADDRESS}\\"}`],
    ['percent-encoded JSON', `%7B%22email%22%3A%22${ENCODED}%22%7D`],
    ['lower-case percent-encoding', `%7b%22email%22%3a%22${ENCODED}%22%7d`],
    ['a router state', `__PAGE__?{"email":"${ADDRESS}"}`],
  ])('removes an address written as %s', (_name, text) => {
    const scrubbed = scrubEvent({ message: text, extra: { nested: [text] } });

    expectNoLeak(scrubbed);
  });

  it('keeps the rest of a JSON string', () => {
    expect(scrubEvent({ message: `{"email":"${ADDRESS}","x":1}` })?.message).toBe(
      `{"email":"${FILTERED}","x":1}`
    );
  });

  it('leaves an email key alone outside a query, but still replaces the address it holds', () => {
    const scrubbed = scrubEvent({ user: { email: ADDRESS, id: '7' }, extra: { email: ADDRESS } });

    expect(scrubbed).toEqual({ user: { email: FILTERED, id: '7' }, extra: { email: FILTERED } });
  });

  it('does not mutate the original event', () => {
    const url = `https://x.test/alerts?email=${ENCODED}`;
    const event = {
      request: { url, headers: { referer: url } },
      breadcrumbs: [{ data: { to: url } }],
    };

    scrubEvent(event);

    expect(event.request.url).toBe(url);
    expect(event.request.headers.referer).toBe(url);
    expect(event.breadcrumbs[0].data.to).toBe(url);
  });

  it('never throws, and drops the event when it cannot be walked safely', () => {
    const hostile = {
      get request(): never {
        throw new Error('boom');
      },
    };

    expect(() => scrubEvent(hostile)).not.toThrow();
    expect(scrubEvent(hostile)).toBeNull();
  });

  it('survives malformed percent-encoding and very long input', () => {
    const long = `?email=${'%40'.repeat(50_000)}&x=1`;
    const event = { message: '/a?q=%E0%A4%A&email=%zz', extra: { long } };

    const scrubbed = scrubEvent(event);

    expect(scrubbed).not.toBeNull();
    expect(JSON.stringify(scrubbed)).not.toContain('%zz');
    expect(scrubbed?.extra.long).toBe(`?email=${FILTERED}&x=1`);
  });

  it('costs time in proportion to input built to make a pattern backtrack', () => {
    expectLinear((n) => {
      const hostile = `?${'a'.repeat(n)}&${'e'.repeat(n)}=${'&'.repeat(n / 4)}`;
      scrubEvent({ message: hostile, extra: { query: hostile } });
    }, 200_000);
  });
});
