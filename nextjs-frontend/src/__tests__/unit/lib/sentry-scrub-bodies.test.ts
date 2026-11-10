import { describe, expect, it } from 'vitest';
import { scrubEvent } from '@/lib/sentry/scrub-event';

/** The Saved action's POST body is the visitor's list of slugs: it must never reach monitoring (FREEJ-28). */
const BODY = '[["glow-boosting-face-cream","coffee-discovery-pack"]]';

describe('scrubEvent: request bodies', () => {
  it('removes request.data from an event and keeps the rest of the request', () => {
    const event = {
      request: {
        url: 'https://freejoy.test/saved',
        method: 'POST',
        data: BODY,
        headers: { 'content-type': 'application/json' },
      },
    };

    const scrubbed = scrubEvent(event)!;

    expect(scrubbed.request).not.toHaveProperty('data');
    expect(scrubbed.request).toMatchObject({ url: 'https://freejoy.test/saved', method: 'POST' });
    expect(JSON.stringify(scrubbed)).not.toContain('glow-boosting');
  });

  it('removes the request body attribute from spans, the trace context and breadcrumbs', () => {
    const event = {
      contexts: { trace: { data: { 'http.request.body.data': BODY, 'http.method': 'POST' } } },
      spans: [
        {
          description: 'POST /saved',
          data: { 'http.request.body.data': BODY, 'http.status_code': 200 },
        },
      ],
      breadcrumbs: [{ category: 'http', data: { 'http.request.body.data': BODY, url: '/saved' } }],
    };

    const scrubbed = scrubEvent(event)!;
    const text = JSON.stringify(scrubbed);

    expect(text).not.toContain('glow-boosting');
    expect(text).not.toContain('http.request.body.data');
    expect(text).toContain('http.status_code');
    expect(text).toContain('http.method');
  });

  it('takes request.data out even when it is an object', () => {
    const event = { request: { data: { slugs: ['glow-boosting-face-cream'] }, method: 'POST' } };

    expect(JSON.stringify(scrubEvent(event))).not.toContain('glow-boosting');
  });

  it('does not touch the event it was given', () => {
    const event = { request: { data: BODY } };

    scrubEvent(event);

    expect(event.request.data).toBe(BODY);
  });

  it('leaves an event with no body alone', () => {
    const event = {
      request: { url: 'https://freejoy.test/freebies' },
      spans: [{ data: { a: 1 } }],
    };

    expect(scrubEvent(event)).toEqual(event);
  });
});
