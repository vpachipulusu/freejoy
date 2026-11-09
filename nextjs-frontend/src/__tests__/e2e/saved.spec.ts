import { test, expect } from './helpers/fixtures';
import { waitForPageReady } from './helpers/e2e-utils';
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

const STORAGE_KEY = 'freejoy.saved.v1';

const VIEWPORTS = [
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'mobile 390px', width: 390, height: 844 },
] as const;

async function storedSlugs(page: Page): Promise<string[]> {
  return page.evaluate((key) => {
    const raw = window.localStorage.getItem(key);
    return raw === null ? [] : (JSON.parse(raw) as { slugs: string[] }).slugs;
  }, STORAGE_KEY);
}

/** Presses the first heart on the page and returns the offer title taken from its accessible name. */
async function saveFirstOffer(page: Page): Promise<string> {
  const heart = page.getByRole('button', { name: /^Save / }).first();
  await expect(heart).toBeVisible();
  const label = (await heart.getAttribute('aria-label')) ?? '';
  const title = label.replace(/^Save /, '');
  await heart.click();
  await expect(page.getByRole('button', { name: `Remove ${title} from saved` })).toHaveAttribute(
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

      await page.getByRole('button', { name: `Remove ${title} from saved` }).click();

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
        page.getByRole('button', { name: `Remove ${title} from saved` })
      ).toHaveAttribute('aria-pressed', 'true');

      await page.getByRole('link', { name: title }).first().click();
      await expect(page).toHaveURL(/\/freebies\/[^/?#]+$/);
      await waitForPageReady(page);
      await expect(
        page.getByRole('button', { name: `Remove ${title} from saved` })
      ).toHaveAttribute('aria-pressed', 'true');
    });

    test('toggles from the keyboard', async ({ page }) => {
      await page.goto('/freebies');
      await waitForPageReady(page);
      const heart = page.getByRole('button', { name: /^Save / }).first();

      await heart.focus();
      await page.keyboard.press('Enter');
      await expect(
        page.getByRole('button', { name: /^Remove .* from saved$/ }).first()
      ).toHaveAttribute('aria-pressed', 'true');
      await page.keyboard.press('Space');
      await expect(heart).toHaveAttribute('aria-pressed', 'false');
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
  await page.goto('/freebies');
  await waitForPageReady(page);
  await saveFirstOffer(page);

  const other = await browser.newContext();
  const otherPage = await other.newPage();
  await otherPage.goto('/saved');
  await waitForPageReady(otherPage);

  await expect(otherPage.getByRole('heading', { name: 'Nothing saved yet' })).toBeVisible();
  expect(await context.cookies()).toEqual([]);
  await context.close();
  await other.close();
});
