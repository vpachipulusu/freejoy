import type { Page } from '@playwright/test';
import { test, expect } from './helpers/fixtures';
import { STORAGE_STATE, clickNavLink, waitForPageReady } from './helpers/e2e-utils';

/**
 * Page loading indicator (FREEJ-67): a thin bar and a spinner appear quickly when the person moves to another
 * page and stay until the new page has rendered (src/components/site/navigation-progress.tsx).
 *
 * Each test holds back the server's answer for the page it navigates to, so the loading phase lasts as long as
 * the test needs, asserts the indicator, releases the answer, and asserts the indicator is gone and the page is
 * there. Run against a production build (`npm run test:e2e:local`), never `next dev`.
 */

const INDICATOR = 'navigation-progress';
const RELEASE_FALLBACK_MS = 20_000;

/** Holds the next navigation answers for `url` until `release()` is called (or a fallback timer, so a failed test cannot hang). */
async function holdNavigation(page: Page, url: RegExp, options: { documents: boolean }) {
  let release: () => void = () => undefined;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
    setTimeout(resolve, RELEASE_FALLBACK_MS).unref?.();
  });

  await page.route(url, async (route) => {
    const request = route.request();
    const headers = request.headers();
    const isPrefetch =
      !!headers['next-router-prefetch'] || !!headers['next-router-segment-prefetch'];
    const isRouterNavigation = !!headers['rsc'] && !isPrefetch;
    const isDocument = request.resourceType() === 'document';

    if (isRouterNavigation || (options.documents && isDocument)) await gate;
    await route.continue();
  });

  return release;
}

test.describe('Page loading indicator', () => {
  test.describe('Public pages', () => {
    test('shows while a public page loads and goes away when it has rendered', async ({ page }) => {
      await page.goto('/');
      await waitForPageReady(page);
      const indicator = page.getByTestId(INDICATOR);

      // never on the first page load
      await expect(indicator).toHaveCount(0);

      const release = await holdNavigation(page, /\/freebies(\?|$)/, { documents: false });
      await clickNavLink(
        page,
        page.locator('header').getByRole('link', { name: 'All freebies' }).first()
      );

      // the server has not answered yet: still on the home page, with the indicator and its spinner
      await expect(indicator).toBeVisible();
      await expect(page.getByRole('status', { name: 'Loading' }).first()).toBeAttached();
      await expect(page).toHaveURL(/\/$/);

      release();
      await expect(page).toHaveURL(/\/freebies$/);
      await expect(page.getByRole('heading', { level: 2, name: 'All freebies' })).toBeVisible();
      await expect(indicator).toHaveCount(0);
    });

    test('does not appear for a link that only changes the hash', async ({ page }) => {
      await page.goto('/about');
      await waitForPageReady(page);

      // the skip link is off screen until it has focus, so reach it from the keyboard, as a person does
      await page.getByRole('link', { name: 'Skip to main content' }).focus();
      await page.keyboard.press('Enter');
      await expect(page).toHaveURL(/\/about#main-content$/);

      await expect(page.getByTestId(INDICATOR)).toHaveCount(0);
    });

    test('does not block clicks while it shows and does not move the layout', async ({ page }) => {
      await page.goto('/');
      await waitForPageReady(page);
      const release = await holdNavigation(page, /\/freebies(\?|$)/, { documents: false });
      const link = page.locator('header').getByRole('link', { name: 'All freebies' }).first();
      const before = await link.boundingBox();

      await clickNavLink(page, link);
      const indicator = page.getByTestId(INDICATOR);
      await expect(indicator).toBeVisible();

      await expect(indicator).toHaveCSS('pointer-events', 'none');
      await expect(indicator).toHaveCSS('position', 'fixed');
      expect(await link.boundingBox()).toEqual(before);

      release();
      await expect(indicator).toHaveCount(0);
    });
  });

  test.describe('Admin pages', () => {
    test.skip(!process.env.TEST_ADMIN_EMAIL, 'Skipped: TEST_ADMIN_EMAIL not configured');
    test.use({ storageState: STORAGE_STATE.admin });

    test('shows when moving between admin pages under the shared layout', async ({ page }) => {
      await page.goto('/admin');
      await waitForPageReady(page);
      const indicator = page.getByTestId(INDICATOR);
      await expect(indicator).toHaveCount(0);

      const release = await holdNavigation(page, /\/admin\/users(\?|$)/, { documents: false });
      await clickNavLink(page, page.locator('aside').getByRole('link', { name: /^users$/i }));

      await expect(indicator).toBeVisible();

      release();
      await expect(page).toHaveURL(/\/admin\/users$/);
      await expect(page.getByRole('heading', { level: 1, name: 'User Management' })).toBeVisible();
      await expect(indicator).toHaveCount(0);
    });

    test('shows while the admin search form (a GET form) loads the results', async ({ page }) => {
      await page.goto('/admin/users');
      await waitForPageReady(page);
      const indicator = page.getByTestId(INDICATOR);

      const release = await holdNavigation(page, /\/admin\/users\?q=/, { documents: true });
      await page.getByRole('searchbox', { name: /search users/i }).fill('freejoy');
      await page.getByRole('searchbox', { name: /search users/i }).press('Enter');

      await expect(indicator).toBeVisible();

      release();
      await expect(page).toHaveURL(/\/admin\/users\?q=freejoy/);
      await expect(page.getByTestId(INDICATOR)).toHaveCount(0);
    });
  });
});
