import { describe, expect, it } from 'vitest';
import { scrubBreadcrumb } from '@/lib/sentry/scrub-breadcrumb';

describe('scrubBreadcrumb', () => {
  it('blanks a click on a Save heart, so the name of an offer is never reported', () => {
    const crumb = {
      category: 'ui.click',
      message: 'main > article > div > button[aria-label="Save Glow-boosting face cream"]',
      data: { target: 'button' },
    };

    const scrubbed = scrubBreadcrumb(crumb);

    expect(scrubbed).not.toBeNull();
    expect(scrubbed?.message).toBe('');
    expect(JSON.stringify(scrubbed)).not.toContain('Glow-boosting');
  });

  it('leaves every other click, and every other kind of breadcrumb, alone', () => {
    const other = { category: 'ui.click', message: 'nav > a[href="/freebies"]' };
    const navigation = { category: 'navigation', data: { from: '/', to: '/saved' } };
    const saveInConsole = { category: 'console', message: 'aria-label="Save x"' };

    expect(scrubBreadcrumb(other)).toEqual(other);
    expect(scrubBreadcrumb(navigation)).toEqual(navigation);
    expect(scrubBreadcrumb(saveInConsole)).toEqual(saveInConsole);
  });

  it('does not change the breadcrumb it was given', () => {
    const crumb = { category: 'ui.click', message: 'button[aria-label="Save X"]' };

    scrubBreadcrumb(crumb);

    expect(crumb.message).toBe('button[aria-label="Save X"]');
  });
});
