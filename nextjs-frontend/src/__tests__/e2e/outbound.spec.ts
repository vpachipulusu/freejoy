import { test, expect } from './helpers/fixtures';
import { waitForPageReady } from './helpers/e2e-utils';
import { expectNoHorizontalOverflow } from './helpers/page-checks';
import { BLOCK_EXTERNAL_HOSTS, NAME_NOT_RESOLVED } from './helpers/block-external-hosts';
import { DB_SEAM, clickCount, offerIdForSlug } from './helpers/db-seam';

/**
 * Money path 1 (FREEJ-27): anonymous browse -> offer -> tracked outbound click, on a production build, the LIVE
 * catalogue and a seeded backend. Runs in the money-desktop and money-mobile (390px) projects, with no login.
 *
 *   npm run test:e2e:local -- outbound alerts-lifecycle      (setup and variables: docs/developer/local-e2e.md)
 *
 * What it relies on (all from `python -m commands.seed_catalogue` against a TEST database):
 *  - published sample offers whose affiliate link is `https://example.com/go/<slug>`, which the backend follows only
 *    when ENVIRONMENT is local or test;
 *  - one ended sample offer, `laundry-sheets-sample` (E2E_ENDED_OFFER_SLUG overrides it). Its id, the key of
 *    `/go/<id>`, comes from GO_E2E_ENDED_OFFER_ID (the variable go-redirect.spec.ts uses) or from the database seam.
 * The database seam (E2E_PSQL, helpers/db-seam.ts) is how a click row is checked: neither the browser nor the Next.js
 * server can read the database. Without it the row assertions skip and everything else still runs.
 *
 * The browser never leaves the site: every host except localhost fails to resolve (helpers/block-external-hosts.ts), so
 * the spec sees the real redirect hop and the provider request die at name resolution; nothing reaches the provider.
 * (page.route cannot stub the provider: Chromium does not route the target of a redirect.)
 *
 * The redirect to the provider is a 307 (src/lib/outbound/respond.ts, docs/developer/freejoy-affiliate.md), not the
 * 302 the story text says; the ended-offer redirect is the 302. The assertions follow the implementation.
 */

test.skip(
  process.env.E2E_LIVE_CATALOGUE !== '1',
  'Needs the live catalogue (E2E_LIVE_CATALOGUE=1) and a seeded backend, see the header of this file'
);

const PROVIDER_ORIGIN = 'https://example.com';
const CATEGORY_SLUG = 'beauty';
const ENDED_SLUG = process.env.E2E_ENDED_OFFER_SLUG ?? 'laundry-sheets-sample';
const OUTBOUND_REL = 'sponsored nofollow noopener';
// The click is committed before the redirect answers, so a row would already be there; this only bounds the wait.
const NO_CLICK_POLL_MS = 2_000;
const PHONE_WIDTHS = [390, 320] as const;

test.use({ launchOptions: { args: BLOCK_EXTERNAL_HOSTS } });

function endedOfferId(): string | null {
  const fromEnv = process.env.GO_E2E_ENDED_OFFER_ID;
  if (fromEnv) return fromEnv;
  return DB_SEAM ? offerIdForSlug(ENDED_SLUG) : null;
}

test.describe('Money path: browse, offer, tracked outbound click', () => {
  test('home to category to offer to the provider: sponsored link, 307, one click row', async ({
    page,
    context,
    baseURL,
    isMobile,
  }) => {
    const origin = new URL(baseURL ?? 'http://localhost:3000');
    const deviceClass = isMobile ? 'MOBILE' : 'DESKTOP';
    await test.step('browse from the home page to a category', async () => {
      await page.goto('/');
      await waitForPageReady(page);
      await expectNoHorizontalOverflow(page, 'the home page');
      await page.locator(`main a[href="/category/${CATEGORY_SLUG}"]`).first().click();
      await expect(page).toHaveURL(new RegExp(`/category/${CATEGORY_SLUG}$`));
      await expect(page.getByRole('heading', { level: 1, name: /freebies$/ })).toBeVisible();
      await expectNoHorizontalOverflow(page, 'the category page');
    });

    await test.step('open an offer from the category', async () => {
      await page.getByRole('article').first().getByRole('link').first().click();
      await expect(page).toHaveURL(/\/freebies\/[a-z0-9-]+$/);
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
      await expectNoHorizontalOverflow(page, 'the offer page');
    });
    const slug = new URL(page.url()).pathname.split('/').pop() ?? '';

    // One call to action, a first-party link with the paid-link relations, and no direct link to the brand.
    const cta = page.locator('a[href^="/go/"]');
    await expect(cta).toHaveCount(1);
    await expect(cta).toHaveAttribute('rel', OUTBOUND_REL);
    await expect(cta).toHaveAttribute('href', /^\/go\/[A-Za-z0-9_-]{1,64}\?nd=1$/);
    // The seeded offers are EDITORIAL with a `sample` link, which earns no commission (FREEJ-60), so neither the
    // label nor either commission sentence is shown, and the link is marked "not disclosed" with ?nd=1. An offer
    // that earns would show both and a plain link; that is covered by unit tests, because a real network host
    // cannot be seeded here.
    await expect(page.getByText('Ad', { exact: true })).toHaveCount(0);
    await expect(page.getByText(/paid a commission|may also earn a commission/i)).toHaveCount(0);
    await expect(page.locator('main a[href^="http"]')).toHaveCount(0);
    const offerId = (await cta.getAttribute('href'))?.replace('/go/', '').replace('?nd=1', '') ?? '';

    const clicksBefore = DB_SEAM
      ? clickCount(offerId, { userAgentClass: deviceClass, referrerHost: origin.hostname })
      : null;
    const arrival = page.waitForEvent('requestfailed', (request) =>
      request.url().startsWith(PROVIDER_ORIGIN)
    );

    await cta.click();
    const providerRequest = await arrival;

    await test.step('the click is a redirect to the stored https destination', async () => {
      const hop = providerRequest.redirectedFrom();
      expect(hop?.url(), 'the provider request followed a redirect from /go/').toBe(
        `${origin.origin}/go/${offerId}?nd=1`
      );
      const redirect = await hop?.response();
      expect(redirect?.status()).toBe(307);
      expect(redirect?.headers()['location']).toBe(`${PROVIDER_ORIGIN}/go/${slug}`);
      expect(redirect?.headers()['cache-control']).toBe('no-store');
      expect(redirect?.headers()['referrer-policy']).toBe('strict-origin-when-cross-origin');
      expect(redirect?.headers()['set-cookie']).toBeUndefined();
      // The provider request is the next hop of that redirect, and it could not leave the machine.
      expect(providerRequest.url()).toBe(`${PROVIDER_ORIGIN}/go/${slug}`);
      expect(providerRequest.failure()?.errorText).toContain(NAME_NOT_RESOLVED);
      expect(await context.cookies(origin.origin)).toEqual([]);
    });

    await test.step('one click row is recorded: coarse device class and referrer host only', async () => {
      if (!DB_SEAM) {
        test.info().annotations.push({
          type: 'not checked',
          description: 'the click row: E2E_PSQL is not set',
        });
        return;
      }
      await expect
        .poll(() =>
          clickCount(offerId, { userAgentClass: deviceClass, referrerHost: origin.hostname })
        )
        .toBe((clicksBefore ?? 0) + 1);
    });
  });

  test('an ended offer has no call to action, and /go/<id> returns to its page with the ended notice', async ({
    page,
    request,
  }) => {
    const endedId = endedOfferId();
    test.skip(
      !endedId,
      'Set GO_E2E_ENDED_OFFER_ID (or E2E_PSQL, to look it up) for the ended sample offer'
    );
    const clicksBefore = DB_SEAM && endedId ? clickCount(endedId) : null;

    const response = await page.goto(`/freebies/${ENDED_SLUG}`);
    await waitForPageReady(page);
    expect(
      response?.status(),
      'a 404 means the offer ended over 30 days ago: re-run commands.seed_catalogue'
    ).toBe(200);
    await expect(page.getByRole('heading', { name: 'This offer has ended' })).toBeVisible();
    await expect(page.locator('a[href^="/go/"]')).toHaveCount(0);
    await expect(
      page.getByRole('button', { name: /get this|start the|enter this|request/i })
    ).toHaveCount(0);
    await expect(page.locator('meta[name="robots"]').first()).toHaveAttribute('content', /noindex/);

    // The raw answer: a relative 302 (so no Host header can choose the origin), nothing cacheable, no cookie.
    const raw = await request.get(`/go/${endedId}`, { maxRedirects: 0 });
    expect(raw.status()).toBe(302);
    expect(raw.headers()['location']).toBe(`/freebies/${ENDED_SLUG}?ended=1`);
    expect(raw.headers()['cache-control']).toBe('no-store');
    expect(raw.headers()['set-cookie']).toBeUndefined();

    // In the browser the visitor lands on the ended page, still without a way out to the provider.
    await page.goto(`/go/${endedId}`);
    await waitForPageReady(page);
    await expect(page).toHaveURL(new RegExp(`/freebies/${ENDED_SLUG}\\?ended=1$`));
    await expect(page.getByRole('heading', { name: 'This offer has ended' })).toBeVisible();
    await expect(page.locator('a[href^="/go/"]')).toHaveCount(0);

    if (DB_SEAM && endedId) {
      await expect
        .poll(() => clickCount(endedId), {
          message: 'an ended offer never records a click',
          timeout: NO_CLICK_POLL_MS,
        })
        .toBe(clicksBefore);
    }
  });

  for (const width of PHONE_WIDTHS) {
    test(`the offer page fits a ${width}px phone with its call to action in reach`, async ({
      page,
    }) => {
      await page.setViewportSize({ width, height: 800 });
      await page.goto(`/category/${CATEGORY_SLUG}`);
      await waitForPageReady(page);
      await page.getByRole('article').first().getByRole('link').first().click();
      await expect(page).toHaveURL(/\/freebies\/[a-z0-9-]+$/);
      await waitForPageReady(page);

      await expectNoHorizontalOverflow(page, `the offer page at ${width}px`);
      const cta = page.locator('a[href^="/go/"]');
      await cta.scrollIntoViewIfNeeded();
      const box = await cta.boundingBox();
      expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
      expect(box?.x ?? -1, 'the CTA starts inside the viewport').toBeGreaterThanOrEqual(0);
      expect(
        (box?.x ?? width) + (box?.width ?? width),
        'the CTA ends inside the viewport'
      ).toBeLessThanOrEqual(width);
    });
  }
});
