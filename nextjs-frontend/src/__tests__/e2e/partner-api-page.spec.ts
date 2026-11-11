import { test, expect } from './helpers/fixtures';
import { waitForPageReady } from './helpers/e2e-utils';
import { expectNoHorizontalOverflow } from './helpers/page-checks';

/**
 * The public partner API documentation (FREEJ-59). It needs no backend: it is built from a committed document.
 * Code examples and tables scroll inside their own focusable regions, so the page itself must never scroll sideways,
 * even on a 320px phone (WCAG 1.4.10 Reflow).
 */

const PATH = '/developers/partner-api';

test.describe('partner API documentation', () => {
  test('fits a 320px phone without scrolling the page sideways', async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 640 });
    await page.goto(PATH);
    await waitForPageReady(page);

    await expect(
      page.getByRole('heading', { level: 1, name: 'Freejoy partner API' })
    ).toBeVisible();
    await expectNoHorizontalOverflow(page, 'the partner API page at 320px');
  });

  test('its code examples and tables scroll inside their own regions and take keyboard focus', async ({
    page,
  }) => {
    await page.setViewportSize({ width: 320, height: 640 });
    await page.goto(PATH);
    await waitForPageReady(page);

    const request = page.getByRole('region', {
      name: /^Example request for GET \/partner\/v1\/clicks$/,
    });
    await request.focus();
    await expect(request).toBeFocused();
    const scrolls = await request.evaluate((element) => element.scrollWidth > element.clientWidth);
    expect(scrolls).toBe(true);
    await page.keyboard.press('ArrowRight');
    await expect.poll(() => request.evaluate((element) => element.scrollLeft)).toBeGreaterThan(0);
    await expectNoHorizontalOverflow(page, 'the partner API page after scrolling an example');
  });

  test('the contents links reach their endpoint heading', async ({ page }) => {
    await page.goto(PATH);
    await waitForPageReady(page);

    await page.getByRole('link', { name: 'GET /partner/v1/clicks' }).first().click();

    await expect(page).toHaveURL(/#operation-partner-clicks$/);
    await expect(
      page.getByRole('heading', { level: 3, name: 'GET /partner/v1/clicks' })
    ).toBeInViewport();
  });

  test('offers the API description for download and marks it noindex', async ({
    page,
    request,
  }) => {
    await page.goto(PATH);
    await waitForPageReady(page);

    const href = await page
      .getByRole('link', { name: /Download the OpenAPI document/ })
      .getAttribute('href');
    expect(href).toBe('/partner-openapi.json');
    const response = await request.get('/partner-openapi.json');
    expect(response.status()).toBe(200);
    expect(response.headers()['x-robots-tag']).toBe('noindex');
    expect((await response.json()).info.title).toBe('Freejoy Partner API');
  });
});
