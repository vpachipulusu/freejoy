import { describe, expect, it } from 'vitest';
import { FILTERED, scrubEvent } from '@/lib/sentry/scrub-event';
import { expectLinear } from '@/__tests__/unit/helpers/linear-growth';

const ADDRESS = 'victim@example.com';
const ENCODED = 'victim%40example.com';

function expectNoLeak(value: unknown) {
  const text = JSON.stringify(value);
  for (const leak of ['victim', 'example.com', '%40', '%2540', 'abc']) {
    expect(text).not.toContain(leak);
  }
}

/**
 * An object or array that counts how many times `scrubEvent` walks it (reads its keys, or maps over it).
 * The count is the work done, so the tests below prove "each node is walked once" without a clock.
 */
function walked<T extends object>(target: T, counter: { walks: number }): T {
  return new Proxy(target, {
    ownKeys(node) {
      counter.walks += 1;
      return Reflect.ownKeys(node);
    },
    get(node, key, receiver) {
      if (key === 'map') counter.walks += 1;
      return Reflect.get(node, key, receiver);
    },
  });
}

describe('scrubEvent: cycles, shared references and depth', () => {
  it('walks an object with six self-references once and leaks nothing', () => {
    const counter = { walks: 0 };
    const node = walked<Record<string, unknown>>(
      { url: `https://x.test/a?email=${ENCODED}` },
      counter
    );
    for (let i = 0; i < 6; i++) node[`self${i}`] = node;

    const result = scrubEvent({ extra: node });

    // Without cycle detection the walk would branch six ways at every level down to the depth limit
    // (6^12 visits), blow the node budget and return null.
    expect(result).not.toBeNull();
    expect(counter.walks).toBe(1);
    expectNoLeak(result);
    expect(JSON.stringify(result)).toContain('[Circular]');
  });

  it('walks mutually referencing arrays and objects once each', () => {
    const counter = { walks: 0 };
    const a = walked<Record<string, unknown>>({ to: `/a?email=${ENCODED}` }, counter);
    const b = walked<unknown[]>([a, a, a], counter);
    a.list = b;
    a.again = b;

    expectNoLeak(scrubEvent({ breadcrumbs: b }));

    expect(counter.walks).toBe(2); // b once, a once; every other reference is a cycle or a finished child
  });

  it('walks a child shared ten times once, and a diamond chain once per level', () => {
    const urlOf = `https://x.test/a?email=${ENCODED}`;
    const sharedCounter = { walks: 0 };
    const shared = walked<Record<string, unknown>>({ url: urlOf }, sharedCounter);
    const wide = Object.fromEntries(Array.from({ length: 10 }, (_, i) => [`k${i}`, shared]));
    const diamondCounter = { walks: 0 };
    const levels = 9; // the leaf must sit above the depth limit (12) to be walked at all
    let level: Record<string, unknown> = walked<Record<string, unknown>>(
      { url: urlOf },
      diamondCounter
    );
    for (let i = 0; i < levels; i++) {
      level = walked<Record<string, unknown>>({ left: level, right: level }, diamondCounter);
    }

    expectNoLeak(scrubEvent({ extra: wide }));
    expectNoLeak(scrubEvent({ extra: level }));

    // Walked afresh per parent, the diamond would cost 2^9 visits at the bottom.
    expect(sharedCounter.walks).toBe(1);
    expect(diamondCounter.walks).toBe(levels + 1);
  });

  it('drops the event rather than walk an unreasonable number of nodes', () => {
    const huge = { list: Array.from({ length: 150_000 }, (_, i) => ({ i })) };

    expect(scrubEvent(huge)).toBeNull();
  });

  it('keeps ordinary repeated values apart from the cycle marker', () => {
    const shared = { n: 1 };

    expect(scrubEvent({ a: shared, b: shared })).toEqual({ a: { n: 1 }, b: { n: 1 } });
  });

  it('filters an object nested past the depth limit instead of passing it through', () => {
    const url = `https://x.test/a?email=${ENCODED}`;
    let nested: Record<string, unknown> = { url };
    for (let i = 0; i < 13; i++) nested = { next: nested };

    const scrubbed = scrubEvent({ extra: nested });

    expectNoLeak(scrubbed);
    expect(JSON.stringify(scrubbed)).toContain(FILTERED);
  });

  it('filters an array nested past the depth limit', () => {
    let nested: unknown = [`/a?email=${ENCODED}`];
    for (let i = 0; i < 13; i++) nested = [nested];

    expectNoLeak(scrubEvent({ extra: nested }));
  });
});

describe('scrubEvent: gaps in how an address can be written into a URL', () => {
  const URLS = [
    `https://x.test/alerts#email=${ENCODED}&token=abc`,
    `https://x.test/alerts?x=1#/page?email=${ADDRESS}`,
    `https://x.test/alerts?EMAIL%5B%5D=${ENCODED}&token=abc`,
    `https://x.test/alerts?user%5Bemail%5D=${ENCODED}&token=abc`,
    `https://x.test/alerts?Email%5b%5d=${ENCODED}`,
    `https://${ENCODED}@x.test/alerts?token=abc`,
    `https://${ADDRESS}@x.test/alerts`,
    `https://api.test/v3/contacts/${ENCODED}`,
    `https://api.test/v3/contacts/${ADDRESS}`,
    `https://api.test/v3/contacts/victim%2540example.com`,
    `https://x.test/alerts?${ENCODED}`,
    `https://x.test/alerts?q+${ENCODED}`,
    `https://x.test/alerts?${ADDRESS}&token=abc`,
    `https://x.test/alerts?x=1&${ENCODED}`,
    `https://x.test/alerts?q=${'a'.repeat(300)}.${ADDRESS}`,
  ];

  it.each(URLS)('removes the address from %s everywhere it is reported', (url) => {
    const scrubbed = scrubEvent({
      request: { url, headers: { referer: url } },
      transaction: `GET ${url}`,
      breadcrumbs: [
        { category: 'navigation', data: { from: url, to: url } },
        { category: 'fetch', data: { url } },
      ],
    });

    const text = JSON.stringify(scrubbed);
    expect(text).not.toContain('victim');
    expect(text).not.toContain('example.com');
    expect(text).not.toContain('abc"');
  });

  it('replaces a bare address in any string, plain or percent-encoded', () => {
    const scrubbed = scrubEvent({
      message: `Failed for ${ADDRESS} and ${ENCODED} and VICTIM@EXAMPLE.CO.UK.`,
      user: { email: ADDRESS },
    });

    expectNoLeak(scrubbed);
    expect(scrubbed?.message).toBe(`Failed for ${FILTERED} and ${FILTERED} and ${FILTERED}.`);
  });

  it('keeps stack frames, decorators, scoped packages and host-only text unchanged', () => {
    const event = {
      exception: {
        values: [
          {
            stacktrace: {
              frames: [
                { filename: 'node_modules/@sentry/core/build/index.js', lineno: 3 },
                { filename: 'app:///node_modules/@radix-ui/react-dialog/dist/index.mjs' },
                { filename: 'node_modules/.pnpm/@sentry+core@8.1.0/node_modules/x.js' },
                { context_line: '  @Injectable()', pre_context: ['@Component({'] },
              ],
            },
          },
        ],
      },
      message: 'ssh user@host failed; see npm@10.9.1 and @scope/pkg',
    };

    expect(scrubEvent(event)).toEqual(event);
  });

  const ADDRESS_LIKE_RUNS: ReadonlyArray<readonly [string, (n: number) => string]> = [
    ['letters', (n) => 'a'.repeat(n)],
    ['letters and a trailing @', (n) => `${'a'.repeat(n)}@`],
    ['encoded @ signs', (n) => '%40'.repeat(n / 2)],
    ['alternating letters and @', (n) => `${'a@'.repeat(n / 2)}b`],
    ['a domain of single-letter labels', (n) => `x@${'a.'.repeat(n / 2)}`],
  ];

  it.each(ADDRESS_LIKE_RUNS)('costs time in proportion to a long run of %s', (_name, build) => {
    expectLinear((n) => {
      const run = build(n);
      scrubEvent({ message: run, extra: { url: `/a?${run}` } });
    }, 200_000);
  });
});

describe('scrubEvent: parameter-name and JSON-name patterns stay linear', () => {
  const REPEATED: ReadonlyArray<readonly [string, (n: number) => string]> = [
    ['a query of repeated email', (n) => `?${'email'.repeat(n)}`],
    ['repeated email', (n) => 'email'.repeat(n)],
    ['a quote then repeated email', (n) => `"${'email'.repeat(n)}`],
    ['an escaped quote then repeated email', (n) => `\\"${'email'.repeat(n)}`],
    ['repeated percent-encoded email', (n) => '%22email%22'.repeat(n)],
  ];
  const SIZES = [
    ['20 000 repeats', 20_000],
    ['about 1 MB', 200_000],
  ] as const;

  for (const [name, build] of REPEATED) {
    for (const [size, repeats] of SIZES) {
      it(`${name}, ${size}, costs time in proportion to its length`, () => {
        expectLinear((n) => {
          const text = build(n);
          scrubEvent({ message: text, request: { url: text } });
        }, repeats);
      });
    }
  }
});

describe('scrubEvent: addresses with letters outside ASCII', () => {
  it.each([
    'jösé@example.com',
    'josé@example.com',
    'victim@bücher.example',
    'victim@exämple.com',
    'victim%40ex%C3%A4mple.com',
    'victim@exam%70le.com',
    'ünïcode@exämple.co.uk',
  ])('replaces %s wherever it appears', (address) => {
    const url = `https://x.test/contacts/${address}?q=${address}`;
    const scrubbed = scrubEvent({
      message: `for ${address}.`,
      request: { url },
      breadcrumbs: [{ data: { to: url, from: `/a/${address}` } }],
    });

    const text = JSON.stringify(scrubbed);
    expect(text).not.toMatch(/jös|josé|victim|ünï|bücher|exäm|exam%70|%C3/i);
    expect(scrubbed?.message).toBe(`for ${FILTERED}.`);
  });

  it('costs time in proportion to a long run of non-ASCII labels', () => {
    expectLinear((n) => {
      scrubEvent({
        message: `x@${'ä.'.repeat(n)}`,
        extra: { u: `/a?${'ä@'.repeat(n / 2)}` },
      });
    }, 100_000);
  });
});
