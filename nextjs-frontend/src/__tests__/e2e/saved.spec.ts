import { test, expect } from './helpers/fixtures';
import { useDistinctClientIp, waitForPageReady } from './helpers/e2e-utils';
import type { Page } from '@playwright/test';

/**
 * Saved freebies (FREEJ-28): save from the catalogue, open /saved from the header (desktop) or the tab bar
 * (390px), see the card, remove it, and check the list survives a reload and stays on this device.
 *
 * Fixture catalogue (the e2e server runs with CATALOGUE_SOURCE=fixture unless E2E_LIVE_CATALOGUE=1), so no
 * seeded data is needed beyond the running app. Run it with `npm run test:e2e:saved` against a production build
 * served with CATALOGUE_SOURCE=fixture (E2E_BASE_URL points at it); no backend or Firebase project is needed.
 */

test.skip(
  process.env.E2E_LIVE_CATALOGUE === '1',
  'Fixture specs: the server is on the live catalogue'
);

// The Saved action allows 6 calls a minute per visitor address, and with no trusted address header every visitor
// shares one bucket. Start the server with TRUSTED_CLIENT_IP_HEADER=x-e2e-client-ip so each test can have its own
// address (the same opt-in the other specs use for the rate limiter).
test.beforeEach(async ({ page }) => {
  await useDistinctClientIp(page);
});

const STORAGE_KEY = 'freejoy.saved.v1';

const VIEWPORTS = [
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'mobile 390px', width: 390, height: 844 },
] as const;

/** Header widths where the row used to break (1024 to 1310px), and one wide. */
const HEADER_WIDTHS = [1024, 1280, 1366] as const;
/** Phone widths for the card layout: the narrowest phones, then the design's 375 and 390. */
const CARD_WIDTHS = [320, 344, 360, 375] as const;

/** Live fixture offers, so a list long enough to scroll can be put in storage without clicking. */
const SAVED_FIXTURES = [
  'glow-boosting-face-cream-sample',
  'mini-hydrating-lip-balm',
  'first-order-skincare-voucher',
  'overnight-sheet-mask-trial',
  'coffee-discovery-pack',
  'crunchy-snack-selection',
  'everyday-tea-tasting-kit',
  'first-bag-coffee-cashback',
  'calming-herbal-tea-sample',
  'daily-gummy-vitamin-trial',
];

type Box = { x: number; y: number; width: number; height: number };
const intersects = (a: Box, b: Box) =>
  a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;

async function storedSlugs(page: Page): Promise<string[]> {
  return page.evaluate((key) => {
    const raw = window.localStorage.getItem(key);
    return raw === null ? [] : (JSON.parse(raw) as { slugs: string[] }).slugs;
  }, STORAGE_KEY);
}

/** Presses the first heart on the page and returns the offer title taken from its accessible name. */
async function saveFirstOffer(page: Page): Promise<string> {
  const heart = page.getByRole('button', { name: /^Save /, pressed: false }).first();
  await expect(heart).toBeVisible();
  const label = (await heart.getAttribute('aria-label')) ?? '';
  const title = label.replace(/^Save /, '');
  await heart.click();
  await expect(page.getByRole('button', { name: `Save ${title}`, pressed: true })).toHaveAttribute(
    'aria-pressed',
    'true'
  );
  return title;
}

for (const viewport of VIEWPORTS) {
  const isDesktop = viewport.width >= 1024;

  test.describe(`Saved freebies on ${viewport.name}`, () => {
    test.use({ viewport: { width: viewport.width, height: viewport.height } });

    test('save from the catalogue, find it on /saved, remove it', async ({ page }) => {
      await page.goto('/freebies');
      await waitForPageReady(page);

      const title = await saveFirstOffer(page);
      expect(await storedSlugs(page)).toHaveLength(1);

      // The header link on desktop, the "Quick links" tab bar on phones.
      const nav = isDesktop
        ? page.getByRole('banner')
        : page.getByRole('navigation', { name: 'Quick links' });
      await nav.getByRole('link', { name: 'Saved' }).click();
      await expect(page).toHaveURL(/\/saved$/);
      await waitForPageReady(page);

      await expect(page.getByRole('heading', { level: 1, name: 'Saved freebies' })).toBeVisible();
      await expect(page.getByText('Saved on this device')).toBeVisible();
      await expect(page.getByRole('heading', { level: 3, name: title })).toBeVisible();
      await expect(nav.getByRole('link', { name: 'Saved' })).toHaveAttribute(
        'aria-current',
        'page'
      );

      await page.getByRole('button', { name: `Save ${title}`, pressed: true }).click();

      await expect(page.getByRole('heading', { name: 'Nothing saved yet' })).toBeVisible();
      await expect(page.getByRole('link', { name: 'Browse freebies' })).toHaveAttribute(
        'href',
        '/freebies'
      );
      expect(await storedSlugs(page)).toEqual([]);
    });

    test('keeps a saved offer across a reload and shows it saved on its own page', async ({
      page,
    }) => {
      await page.goto('/freebies');
      await waitForPageReady(page);
      const title = await saveFirstOffer(page);

      await page.reload();
      await waitForPageReady(page);
      await expect(
        page.getByRole('button', { name: `Save ${title}`, pressed: true })
      ).toHaveAttribute('aria-pressed', 'true');

      await page.getByRole('link', { name: title }).first().click();
      await expect(page).toHaveURL(/\/freebies\/[^/?#]+$/);
      await waitForPageReady(page);
      await expect(
        page.getByRole('button', { name: `Save ${title}`, pressed: true })
      ).toHaveAttribute('aria-pressed', 'true');
    });

    test('toggles from the keyboard', async ({ page }) => {
      await page.goto('/freebies');
      await waitForPageReady(page);
      const heart = page.getByRole('button', { name: /^Save /, pressed: false }).first();

      await heart.focus();
      await page.keyboard.press('Enter');
      await expect(
        page.getByRole('button', { name: /^Save /, pressed: true }).first()
      ).toHaveAttribute('aria-pressed', 'true');
      await page.keyboard.press('Space');
      await expect(page.getByRole('button', { name: /^Save /, pressed: true })).toHaveCount(0);
    });

    test('treats corrupt stored data as an empty list, drops a missing offer and stays noindex', async ({
      page,
    }) => {
      await page.addInitScript(
        (key) => window.localStorage.setItem(key, '{"v":9,"x":'),
        STORAGE_KEY
      );
      await page.goto('/saved');
      await waitForPageReady(page);

      await expect(page.getByRole('heading', { name: 'Nothing saved yet' })).toBeVisible();
      await expect(page.locator('meta[name="robots"]')).toHaveAttribute('content', /noindex/);
    });

    test('drops a saved offer that no longer exists, without an error', async ({ page }) => {
      await page.addInitScript(
        (key) =>
          window.localStorage.setItem(
            key,
            JSON.stringify({ v: 1, slugs: ['no-such-offer-anymore'] })
          ),
        STORAGE_KEY
      );
      await page.goto('/saved');
      await waitForPageReady(page);

      await expect(page.getByRole('heading', { name: 'Nothing saved yet' })).toBeVisible();
      await expect(page.getByText(/no longer available and was removed/)).toBeVisible();
      // (Next.js keeps an empty role=alert route announcer on every page, so look for our own error panel.)
      await expect(page.getByText('Your saved freebies are not loading')).toHaveCount(0);
      await expect.poll(() => storedSlugs(page)).toEqual([]);
    });
  });
}

test('the Saved list is per browser: a fresh context starts empty and sets no cookie', async ({
  browser,
}) => {
  const context = await browser.newContext();
  const page = await context.newPage();
  await useDistinctClientIp(page);
  await page.goto('/freebies');
  await waitForPageReady(page);
  await saveFirstOffer(page);

  const other = await browser.newContext();
  const otherPage = await other.newPage();
  await useDistinctClientIp(otherPage);
  await otherPage.goto('/saved');
  await waitForPageReady(otherPage);

  await expect(otherPage.getByRole('heading', { name: 'Nothing saved yet' })).toBeVisible();
  expect(await context.cookies()).toEqual([]);
  await context.close();
  await other.close();
});

for (const width of HEADER_WIDTHS) {
  test.describe(`Header at ${width}px`, () => {
    test.use({ viewport: { width, height: 800 } });

    test('keeps the call to action on one line, the heart a real icon, the search usable and nothing scrolling sideways', async ({
      page,
    }) => {
      await page.goto('/freebies');
      await waitForPageReady(page);
      const header = page.getByRole('banner');

      const cta = header.getByRole('link', { name: 'Get free alerts' });
      expect((await cta.boundingBox())!.height).toBeLessThanOrEqual(44);
      const icon = header.locator('a[href="/saved"] svg');
      expect((await icon.boundingBox())!.width).toBe(16);
      const search = header.locator('#site-search');
      expect((await search.boundingBox())!.width).toBeGreaterThanOrEqual(150);
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - window.innerWidth
      );
      expect(overflow).toBe(0);
    });
  });
}

for (const width of CARD_WIDTHS) {
  test.describe(`Offer cards at ${width}px`, () => {
    test.use({ viewport: { width, height: 800 } });

    test('keep the type badge on one short line that meets neither the heart nor the Demo badge', async ({
      page,
    }) => {
      await page.goto('/freebies');
      await waitForPageReady(page);

      const cards = page.locator('main article');
      for (let index = 0; index < 4; index += 1) {
        const card = cards.nth(index);
        const badge = card
          .locator('span')
          .filter({ hasText: /^(Free sample|Free trial|Cashback|Voucher|Competition)$/i })
          .first();
        const badgeBox = (await badge.boundingBox())!;
        const discBox = (await card.locator('button span').first().boundingBox())!;
        const demoBox = (await card.getByText('Demo', { exact: true }).boundingBox())!;

        expect(badgeBox.height).toBeLessThanOrEqual(26);
        expect(intersects(badgeBox, discBox)).toBe(false);
        expect(intersects(badgeBox, demoBox)).toBe(false);
      }
    });
  });
}

test.describe('Hover stays with the control that is hovered', () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test('hovering the title leaves the heart alone, hovering the heart does not underline the link', async ({
    page,
  }) => {
    await page.goto('/freebies');
    await waitForPageReady(page);
    const card = page.locator('main article').first();
    const disc = card.locator('button span').first();
    const label = card.getByText('View freebie');
    const discBackground = () => disc.evaluate((node) => getComputedStyle(node).backgroundColor);
    const underline = () => label.evaluate((node) => getComputedStyle(node).textDecorationLine);

    const before = await discBackground();
    await card.getByRole('heading', { level: 3 }).getByRole('link').hover();
    expect(await discBackground()).toBe(before);
    expect(await underline()).toBe('underline');

    await card.getByRole('button', { name: /^Save / }).hover();
    expect(await underline()).toBe('none');
    expect(await discBackground()).not.toBe(before);
  });
});

test.describe('Removing a card on /saved', () => {
  test.use({ viewport: { width: 1280, height: 480 } });

  test('keeps the scroll position when a card below the fold goes, and the keyboard lands on the next heart', async ({
    page,
  }) => {
    await page.addInitScript(
      ([key, slugs]) => window.localStorage.setItem(key as string, JSON.stringify({ v: 1, slugs })),
      [STORAGE_KEY, SAVED_FIXTURES] as const
    );
    await page.goto('/saved');
    await waitForPageReady(page);
    const hearts = page.getByRole('button', { name: /^Save /, pressed: true });
    await expect(hearts.nth(8)).toBeAttached();

    await hearts.nth(8).focus();
    const y = await page.evaluate(() => {
      window.scrollTo({ top: 200, behavior: 'instant' });
      return window.scrollY;
    });
    expect(y).toBeGreaterThan(0);
    const countBefore = await hearts.count();
    const nextLabel = await hearts.nth(9).getAttribute('aria-label');

    await page.keyboard.press('Enter');

    await expect(hearts).toHaveCount(countBefore - 1);
    expect(await page.evaluate(() => window.scrollY)).toBe(y);
    await expect(page.getByRole('button', { name: nextLabel!, pressed: true })).toBeFocused();
  });
});
