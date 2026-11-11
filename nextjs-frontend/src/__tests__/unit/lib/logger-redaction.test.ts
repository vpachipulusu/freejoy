// @vitest-environment node
import { Writable } from 'node:stream';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const sentry = vi.hoisted(() => ({ captureException: vi.fn(), addBreadcrumb: vi.fn() }));
vi.mock('@sentry/nextjs', () => sentry);

// Pre-seed the logger singleton so importing it never opens real log files.
const winstonFake = vi.hoisted(() => {
  const fake = { error: vi.fn(), warn: vi.fn(), info: vi.fn(), debug: vi.fn() };
  (globalThis as { __winstonLogger?: unknown }).__winstonLogger = fake;
  return fake;
});

import {
  buildRedactionFormat,
  logger,
  redactLogData,
  redactLogEntry,
  redactString,
} from '@/lib/logger';
import { expectLinear } from '@/__tests__/unit/helpers/linear-growth';

// Fake values assembled at runtime so no real-looking secret sits in the source.
const EMAIL = ['jane.doe', 'example.com'].join('@');
const UID = 'aB3dE5gH7jK9mN1pQ3sT5vX7yZ2c'; // 28 chars, Firebase uid shape
const JWT = ['eyJhbGciOiJSUzI1NiJ9', 'eyJzdWIiOiJ1c2VyIn0', 'c2lnbmF0dXJlLWJ5dGVz'].join('.');
// Assembled at runtime so the detect-private-key pre-commit hook does not flag the fixture.
const KEY_LABEL = ['PRIVATE', 'KEY-----'].join(' ');
const PRIVATE_KEY = `-----BEGIN ${KEY_LABEL}\nMIIEvQIBADANBgkqhkiG9w0BAQ\n-----END ${KEY_LABEL}`;
const STRIPE_KEY = ['sk', 'live', 'abcdefghijklmnop1234'].join('_');
const WEBHOOK_SECRET = ['whsec', 'abcdefghijklmnop1234'].join('_');

const LEAKS = [EMAIL, UID, JWT, 'MIIEvQIBADANBgkqhkiG9w0BAQ', STRIPE_KEY, WEBHOOK_SECRET];

function expectNoLeaks(output: string) {
  for (const leak of LEAKS) {
    expect(output).not.toContain(leak);
  }
}

describe('redactString', () => {
  it.each([
    ['email', `lookup failed for ${EMAIL} today`],
    ['firebase uid', `no user record for uid ${UID}`],
    ['jwt', `token ${JWT} rejected`],
    ['bearer token', 'Authorization: Bearer abc.def-ghi_123'],
    ['private key block', `bad key ${PRIVATE_KEY} here`],
    ['stripe secret key', `invalid key ${STRIPE_KEY}`],
    ['webhook secret', `signature mismatch for ${WEBHOOK_SECRET}`],
  ])('removes %s', (_label, input) => {
    const out = redactString(input);
    expectNoLeaks(out);
    expect(out).not.toMatch(/Bearer abc/);
    expect(out).toContain('[REDACTED]');
  });

  it('keeps ordinary text intact', () => {
    expect(redactString('Failed to update profile for user 42')).toBe(
      'Failed to update profile for user 42'
    );
  });

  it('redacts an unterminated private key block', () => {
    const out = redactString(`boom -----BEGIN RSA ${KEY_LABEL}\nMIIEabc`);
    expect(out).not.toContain('MIIEabc');
  });
});

describe('redactLogData', () => {
  it('reduces an Error to name, code and a redacted message only', () => {
    const err = Object.assign(new Error(`There is no user record for ${EMAIL} (${UID})`), {
      code: 'auth/user-not-found',
      name: 'FirebaseAuthError',
      customData: { email: EMAIL },
    });

    const out = redactLogData(err) as Record<string, unknown>;

    expect(Object.keys(out).sort()).toEqual(['code', 'message', 'name']);
    expect(out.name).toBe('FirebaseAuthError');
    expect(out.code).toBe('auth/user-not-found');
    expectNoLeaks(JSON.stringify(out));
  });

  it('reduces a Prisma-shaped error and drops its metadata', () => {
    const err = Object.assign(new Error(`Unique constraint failed on email=${EMAIL}`), {
      name: 'PrismaClientKnownRequestError',
      code: 'P2002',
      meta: { target: ['email'], value: EMAIL },
      clientVersion: '6.0.0',
    });

    const out = redactLogData({ error: err }) as { error: Record<string, unknown> };

    expect(out.error).toEqual({
      name: 'PrismaClientKnownRequestError',
      code: 'P2002',
      message: expect.not.stringContaining(EMAIL),
    });
  });

  it('truncates very long error messages', () => {
    const out = redactLogData(new Error('x'.repeat(5000))) as { message: string };
    expect(out.message.length).toBeLessThanOrEqual(520);
  });

  it('redacts secret-bearing keys regardless of casing', () => {
    const out = redactLogData({
      email: EMAIL,
      firebaseUid: UID,
      uid: UID,
      idToken: 'opaque',
      Authorization: 'Bearer opaque',
      sessionCookie: 'opaque',
      password: 'hunter2',
      stripeSecretKey: 'opaque',
      private_key: PRIVATE_KEY,
      apiKey: 'opaque',
      userId: 'user-42',
      route: '/dashboard',
    }) as Record<string, unknown>;

    expect(out).toEqual({
      email: '[REDACTED]',
      firebaseUid: '[REDACTED]',
      uid: '[REDACTED]',
      idToken: '[REDACTED]',
      Authorization: '[REDACTED]',
      sessionCookie: '[REDACTED]',
      password: '[REDACTED]',
      stripeSecretKey: '[REDACTED]',
      private_key: '[REDACTED]',
      apiKey: '[REDACTED]',
      userId: 'user-42',
      route: '/dashboard',
    });
  });

  it('walks nested objects and arrays', () => {
    const out = redactLogData({
      outer: [{ note: `mail ${EMAIL}` }, { inner: { tokenValue: 'x', msg: JWT } }],
    });
    expectNoLeaks(JSON.stringify(out));
  });

  it('survives circular references and does not mutate the input', () => {
    const input: Record<string, unknown> = { message: `hi ${EMAIL}` };
    input.self = input;

    const out = redactLogData(input) as Record<string, unknown>;

    expect(out.self).toBe('[Circular]');
    expect(input.message).toBe(`hi ${EMAIL}`);
  });

  it('passes through primitives and nullish values', () => {
    expect(redactLogData(null)).toBeNull();
    expect(redactLogData(undefined)).toBeUndefined();
    expect(redactLogData(7)).toBe(7);
    expect(redactLogData(true)).toBe(true);
  });

  it('does not leak through a throwing getter', () => {
    const input = {
      get boom(): string {
        throw new Error(`secret ${EMAIL}`);
      },
      ok: 1,
    };
    const out = redactLogData(input) as Record<string, unknown>;
    expect(out.ok).toBe(1);
    expectNoLeaks(JSON.stringify(out));
  });
});

describe('hardening', () => {
  it('costs time in proportion to oversized input without an @ sign', () => {
    expectLinear((n) => {
      redactString('a'.repeat(n));
      redactLogData({ message: 'a'.repeat(n) });
    }, 200_000);
  });

  it('truncates oversized strings', () => {
    expect(redactString('ab '.repeat(10_000))).toContain('[TRUNCATED]');
  });

  it('never throws on hostile input', () => {
    const revoked = Proxy.revocable({}, {});
    revoked.revoke();
    const badError = new Error('x');
    Object.defineProperty(badError, 'message', {
      get() {
        throw new Error('nope');
      },
    });

    expect(() => redactLogData(revoked.proxy)).not.toThrow();
    expect(() => redactLogData(badError)).not.toThrow();
    expect(() => redactString(Symbol('s') as unknown as string)).not.toThrow();
    expect(() => logger.error(undefined as unknown as string, revoked.proxy)).not.toThrow();
  });

  it('redacts identifying object keys and ignores __proto__', () => {
    const polluted = JSON.parse('{"__proto__": {"admin": true}, "ok": 1}');
    const out = redactLogData({ [EMAIL]: 1, [UID]: 2, nested: polluted }) as Record<
      string,
      unknown
    >;

    expectNoLeaks(JSON.stringify(out));
    expect((out.nested as Record<string, unknown>).ok).toBe(1);
    expect(Object.getPrototypeOf(out.nested)).toBe(Object.prototype);
  });

  it.each([
    ['uid without digits', 'abCdEfGhIjKlMnOpQrStUvWxYzAb'],
    ['prefixed test key', ['x', 'sk', 'test', 'abcdefghijklmnop1234'].join('_')],
    ['headerless base64 body', 'MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC+/=='],
  ])('redacts %s', (_label, secret) => {
    expect(redactString(`failure ${secret} here`)).not.toContain(secret.slice(-12));
  });
});

describe('winston redaction format (console and file transports)', () => {
  async function runThroughWinston(log: (l: import('winston').Logger) => void) {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const winston = require('winston') as typeof import('winston');
    const lines: string[] = [];
    const sink = new Writable({
      write(chunk, _enc, cb) {
        lines.push(chunk.toString());
        cb();
      },
    });
    const instance = winston.createLogger({
      format: winston.format.combine(buildRedactionFormat(winston), winston.format.json()),
      transports: [new winston.transports.Stream({ stream: sink })],
    });
    log(instance);
    await new Promise((resolve) => setImmediate(resolve));
    return lines.join('');
  }

  it('redacts a whole error passed straight to winston', async () => {
    const err = Object.assign(new Error(`no record for ${EMAIL} ${UID}`), {
      code: 'auth/user-not-found',
    });
    const output = await runThroughWinston((l) => l.error('Verify token error:', err));

    expectNoLeaks(output);
    expect(output).toContain('auth/user-not-found');
  });

  it('redacts message text and metadata, including stack text', async () => {
    const output = await runThroughWinston((l) =>
      l.error(`failed for ${EMAIL}`, {
        token: JWT,
        stack: `Error: ${STRIPE_KEY}\n    at fn (file.ts:1:1)`,
        nested: { firebaseUid: UID },
      })
    );

    expectNoLeaks(output);
    expect(JSON.parse(output).level).toBe('error');
  });

  it('redactLogEntry keeps level and drops nothing else', () => {
    const entry = redactLogEntry({ level: 'info', message: `a ${EMAIL}`, route: '/x' });
    expect(entry).toMatchObject({ level: 'info', route: '/x' });
    expect(String(entry.message)).not.toContain(EMAIL);
  });
});

describe('logger wrapper', () => {
  beforeEach(async () => {
    // Let Sentry calls deferred by earlier tests flush before resetting the mocks.
    await new Promise((resolve) => setImmediate(resolve));
    sentry.captureException.mockClear();
    sentry.addBreadcrumb.mockClear();
    Object.values(winstonFake).forEach((fn) => fn.mockClear());
  });

  it('hands winston only name, code and a redacted message for an Error', () => {
    const err = Object.assign(new Error(`bad ${EMAIL} ${UID} ${JWT}`), {
      name: 'FirebaseAuthError',
      code: 'auth/id-token-expired',
    });

    logger.error(`Verify token error for ${EMAIL}:`, err);

    expect(winstonFake.error).toHaveBeenCalledTimes(1);
    const [message, meta] = winstonFake.error.mock.calls[0];
    expectNoLeaks(JSON.stringify([message, meta]));
    expect(Object.keys(meta).sort()).toEqual(['code', 'message', 'name']);
    expect(meta.code).toBe('auth/id-token-expired');
    expect(meta.name).toBe('FirebaseAuthError');
  });

  it('redacts metadata objects containing nested errors', () => {
    logger.error('Stripe failed', {
      customerEmail: EMAIL,
      error: new Error(`key ${STRIPE_KEY}`),
    });

    const [, meta] = winstonFake.error.mock.calls[0];
    expectNoLeaks(JSON.stringify(meta));
    expect(meta.customerEmail).toBe('[REDACTED]');
  });

  it('redacts warn, info and debug metadata too', () => {
    logger.warn('w', { token: JWT });
    logger.info('i', { note: EMAIL });
    logger.debug('d', { uid: UID });

    for (const fn of [winstonFake.warn, winstonFake.info, winstonFake.debug]) {
      expectNoLeaks(JSON.stringify(fn.mock.calls));
    }
  });

  it('sends a redacted error to Sentry', async () => {
    logger.error('Stripe failed', { error: new Error(`key ${STRIPE_KEY} for ${EMAIL}`) });
    await new Promise((resolve) => setImmediate(resolve));

    expect(sentry.captureException).toHaveBeenCalledTimes(1);
    const [captured, ctx] = sentry.captureException.mock.calls[0];
    expect(captured).toBeInstanceOf(Error);
    expectNoLeaks(
      String((captured as Error).message) + String((captured as Error).stack) + JSON.stringify(ctx)
    );
  });

  it('sends redacted breadcrumbs to Sentry', async () => {
    logger.info(`hello ${EMAIL}`, { token: JWT });
    await new Promise((resolve) => setImmediate(resolve));

    expectNoLeaks(JSON.stringify(sentry.addBreadcrumb.mock.calls));
  });
});
