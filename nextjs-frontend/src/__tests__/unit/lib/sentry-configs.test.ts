import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const sentry = vi.hoisted(() => ({
  init: vi.fn(),
  captureRouterTransitionStart: vi.fn(),
  httpIntegration: vi.fn((options: unknown) => ({ name: 'Http', options })),
}));
vi.mock('@sentry/nextjs', () => sentry);

const CONFIGS: ReadonlyArray<readonly [string, () => Promise<unknown>]> = [
  ['client', () => import('../../../../src/instrumentation-client')],
  ['server', () => import('../../../../sentry.server.config')],
  ['edge', () => import('../../../../sentry.edge.config')],
];

const EVENT = {
  request: { url: 'https://x.test/alerts?email=victim%40example.com&token=abc' },
  transaction: 'GET /alerts?email=victim%40example.com',
};

describe('the server config keeps request bodies out of events', () => {
  beforeEach(() => {
    vi.resetModules();
    sentry.init.mockClear();
    sentry.httpIntegration.mockClear();
  });

  it('registers the http integration with incoming request bodies switched off, and keeps request spans on', async () => {
    await import('../../../../sentry.server.config');

    expect(sentry.httpIntegration).toHaveBeenCalledWith({ maxIncomingRequestBodySize: 'none' });
    const options = sentry.init.mock.calls[0][0];
    expect(options.integrations).toEqual([
      { name: 'Http', options: { maxIncomingRequestBodySize: 'none' } },
    ]);
    // Spans of incoming requests stay on: only their bodies go.
    expect(options.tracesSampleRate).toBeGreaterThan(0);
    expect(JSON.stringify(options)).not.toMatch(/disable|ignoreIncomingRequests/i);
  });

  it('does not touch the edge config, which has no http integration', async () => {
    await import('../../../../sentry.edge.config');

    expect(sentry.httpIntegration).not.toHaveBeenCalled();
  });
});

describe('the client config also keeps Save clicks out of breadcrumbs', () => {
  beforeEach(() => {
    vi.resetModules();
    sentry.init.mockClear();
  });

  it('registers beforeBreadcrumb, which blanks a click on a Save heart', async () => {
    await import('../../../../src/instrumentation-client');

    const { beforeBreadcrumb } = sentry.init.mock.calls[0][0];
    expect(typeof beforeBreadcrumb).toBe('function');
    const scrubbed = beforeBreadcrumb({
      category: 'ui.click',
      message: 'button[aria-label="Save Some offer"]',
    });
    expect(scrubbed.message).toBe('');
  });
});

describe('Sentry configs register the scrubber', () => {
  beforeEach(() => {
    vi.resetModules();
    sentry.init.mockClear();
  });
  afterEach(() => vi.unstubAllEnvs());

  it.each(CONFIGS)(
    '%s: beforeSend and beforeSendTransaction filter an address',
    async (_n, load) => {
      await load();

      expect(sentry.init).toHaveBeenCalledTimes(1);
      const options = sentry.init.mock.calls[0][0];
      for (const hook of [options.beforeSend, options.beforeSendTransaction]) {
        expect(typeof hook).toBe('function');
        const text = JSON.stringify(hook(EVENT));
        expect(text).toContain('[Filtered]');
        expect(text).not.toMatch(/victim|example\.com|abc/);
      }
    }
  );
});
