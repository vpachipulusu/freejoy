import { describe, expect, it } from 'vitest';
import { scrubEvent } from '@/lib/sentry/scrub-event';

const RAW = 'http://127.0.0.1:8000/v1/catalogue/offers/glow-boosting-face-cream';

describe('scrubEvent: catalogue offer slugs in span URLs', () => {
  it('turns the slug of a backend offer read into [slug], in the span name and its data', () => {
    const event = {
      spans: [
        {
          description: `GET ${RAW}`,
          data: { 'http.url': RAW, 'url.full': `${RAW}?x=1` },
        },
      ],
    };

    const text = JSON.stringify(scrubEvent(event));

    expect(text).toContain('/v1/catalogue/offers/[slug]');
    expect(text).not.toContain('glow-boosting-face-cream');
    expect(text).toContain('?x=1');
  });

  it('leaves the list read, other catalogue reads and ordinary page URLs unchanged', () => {
    const event = {
      spans: [
        { description: 'GET http://127.0.0.1:8000/v1/catalogue/offers?limit=12' },
        { description: 'GET http://127.0.0.1:8000/v1/catalogue/categories' },
        { description: 'GET https://freejoy.test/freebies/glow-cream' },
      ],
    };

    expect(scrubEvent(event)).toEqual(event);
  });

  it('also covers a slug carried in a breadcrumb or a transaction name', () => {
    const event = {
      transaction: `GET ${RAW}`,
      breadcrumbs: [{ category: 'fetch', data: { url: RAW } }],
    };

    expect(JSON.stringify(scrubEvent(event))).not.toContain('glow-boosting-face-cream');
  });
});
