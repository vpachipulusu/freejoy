import { test, expect } from '@playwright/test';

/**
 * `/go/[offerId]` outbound redirect against a production build and a real, seeded backend.
 *
 * It needs offer ids from the backend database, so it skips unless they are supplied:
 *   GO_E2E_LIVE_OFFER_ID   a published offer with an active affiliate link on an allowed host
 *   GO_E2E_ENDED_OFFER_ID  an offer that was public and has ended (EXPIRED or past end_at)
 *   GO_E2E_DRAFT_OFFER_ID  optional: a draft or archived offer
 *   GO_E2E_LIVE_OFFER_SLUG optional: the slug of the live offer, to click through from its real page
 * With `python -m commands.seed_catalogue` the sample offers use the `sample` provider
 * (`https://example.com/go/<slug>`), which the backend follows only when ENVIRONMENT is local or test.
 * See docs/developer/freejoy-affiliate.md for the commands.
 */

const LIVE = process.env.GO_E2E_LIVE_OFFER_ID;
const ENDED = process.env.GO_E2E_ENDED_OFFER_ID;
const DRAFT = process.env.GO_E2E_DRAFT_OFFER_ID;
const LIVE_SLUG = process.env.GO_E2E_LIVE_OFFER_SLUG;
const HOSTILE_QUERY = '?url=https://evil.example&to=//evil.example&redirect=javascript:alert(1)';

test.describe('/go/[offerId]', () => {
  test.skip(!LIVE, 'GO_E2E_LIVE_OFFER_ID is not set');

  test('answers 307 to the stored https destination, uncached, with no cookie', async ({
    request,
  }) => {
    const response = await request.get(`/go/${LIVE}`, { maxRedirects: 0 });

    expect(response.status()).toBe(307);
    const location = response.headers()['location'];
    expect(new URL(location).protocol).toBe('https:');
    expect(response.headers()['cache-control']).toBe('no-store');
    expect(response.headers()['referrer-policy']).toBe('strict-origin-when-cross-origin');
    expect(response.headers()['set-cookie']).toBeUndefined();
  });

  test('ignores every query parameter: open-redirect attempts never leave the stored destination', async ({
    request,
  }) => {
    const plain = await request.get(`/go/${LIVE}`, { maxRedirects: 0 });
    const attacked = await request.get(`/go/${LIVE}${HOSTILE_QUERY}`, { maxRedirects: 0 });

    expect(attacked.status()).toBe(307);
    expect(attacked.headers()['location']).toBe(plain.headers()['location']);
    expect(attacked.headers()['location']).not.toContain('evil.example');
  });

  test('answers 404 for an unknown or malformed id, never a redirect', async ({ request }) => {
    for (const id of ['no-such-offer', 'a.b', 'a%20b', 'a%3Bb']) {
      const response = await request.get(`/go/${id}${HOSTILE_QUERY}`, { maxRedirects: 0 });
      expect(response.status(), id).toBe(404);
      expect(response.headers()['location'], id).toBeUndefined();
      expect(response.headers()['cache-control'], id).toBe('no-store');
    }
  });

  test('sends an ended offer to its page with ?ended=1', async ({ request }) => {
    test.skip(!ENDED, 'GO_E2E_ENDED_OFFER_ID is not set');

    const response = await request.get(`/go/${ENDED}${HOSTILE_QUERY}`, { maxRedirects: 0 });

    expect(response.status()).toBe(302);
    const location = response.headers()['location'];
    // Relative on purpose: no Host or X-Forwarded-Host can put another origin into it.
    expect(location).toMatch(/^\/freebies\/[a-z0-9-]+\?ended=1$/);
    expect(response.headers()['set-cookie']).toBeUndefined();
  });

  test('does not count a request that is not a top-level navigation', async ({ page }) => {
    await page.goto('/pricing');

    const status = await page.evaluate(
      async (id) => (await fetch(`/go/${id}`, { redirect: 'manual' })).status,
      LIVE
    );

    expect(status).toBe(204);
  });

  test('the real offer page renders a /go/ call to action that redirects to the stored destination', async ({
    page,
    context,
    baseURL,
  }) => {
    test.skip(!LIVE_SLUG, 'GO_E2E_LIVE_OFFER_SLUG is not set');
    const origin = new URL(baseURL ?? 'http://localhost:3000').origin;
    const destination = await page.request
      .get(`/go/${LIVE}`, { maxRedirects: 0 })
      .then((response) => new URL(response.headers()['location']));
    await page.route(`${destination.origin}/**`, (route) =>
      route.fulfill({ status: 200, contentType: 'text/html', body: '<h1>Provider</h1>' })
    );

    await page.goto(`/freebies/${LIVE_SLUG}`);
    const cta = page.locator(`a[href="/go/${LIVE}"]`);
    await expect(cta).toHaveCount(1);
    await expect(cta).toHaveAttribute('rel', 'sponsored nofollow noopener');
    // The seeded links use the `sample` provider, which earns no commission (FREEJ-60), so neither the label nor
    // the commission sentence is shown. An affiliate network link would show both; that is covered by unit tests,
    // because a real network host cannot be seeded here.
    await expect(page.getByText('Ad', { exact: true })).toHaveCount(0);
    await expect(page.getByText(/coming soon|demo offer/i)).toHaveCount(0);
    await cta.click();
    await page.waitForURL(`${destination.origin}/**`);

    expect(new URL(page.url()).origin).toBe(destination.origin);
    expect(await context.cookies(origin)).toEqual([]);
  });

  test('answers a draft or archived offer exactly like an unknown one', async ({ request }) => {
    test.skip(!DRAFT, 'GO_E2E_DRAFT_OFFER_ID is not set');

    const draft = await request.get(`/go/${DRAFT}`, { maxRedirects: 0 });
    const unknown = await request.get('/go/no-such-offer', { maxRedirects: 0 });

    expect(draft.status()).toBe(404);
    expect(await draft.text()).toBe(await unknown.text());
  });

  test('a click in the browser reaches only the stored destination, with the origin as referrer and no cookie', async ({
    page,
    context,
    baseURL,
  }) => {
    const origin = new URL(baseURL ?? 'http://localhost:3000').origin;
    const destination = await page.request
      .get(`/go/${LIVE}`, { maxRedirects: 0 })
      .then((response) => new URL(response.headers()['location']));
    let destinationReferrer: string | undefined;
    let evilRequests = 0;
    page.on('request', (request) => {
      // The navigation itself, not a favicon or other subresource the stub page triggers.
      if (request.url().startsWith(destination.origin) && request.resourceType() === 'document') {
        destinationReferrer = request.headers()['referer'];
      }
    });
    await page.route('**/*', async (route) => {
      const url = new URL(route.request().url());
      if (url.hostname === 'evil.example') {
        evilRequests += 1;
        return route.abort();
      }
      if (url.origin === destination.origin) {
        return route.fulfill({ status: 200, contentType: 'text/html', body: '<h1>Provider</h1>' });
      }
      return route.continue();
    });

    await page.goto('/pricing');
    await page.evaluate(
      ([id, query]) => {
        const anchor = document.createElement('a');
        anchor.id = 'go-link';
        anchor.href = `/go/${id}${query}`;
        anchor.rel = 'sponsored nofollow noopener';
        anchor.textContent = 'Go';
        document.body.prepend(anchor);
      },
      [LIVE, HOSTILE_QUERY]
    );
    await page.locator('#go-link').click();
    await page.waitForURL(`${destination.origin}/**`);

    expect(new URL(page.url()).origin).toBe(destination.origin);
    expect(evilRequests).toBe(0);
    expect(destinationReferrer).toBe(`${origin}/`);
    expect(await context.cookies(origin)).toEqual([]);
  });
});
