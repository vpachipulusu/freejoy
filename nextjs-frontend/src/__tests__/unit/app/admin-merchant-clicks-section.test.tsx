// @vitest-environment node
/**
 * The "Clicks by merchant" section: a labelled GET form (merchant picker and a UTC date range), the note on what
 * a click is, a curated error when the merchant list fails, and a rejected URL value that is never echoed.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';

const mocks = vi.hoisted(() => ({ fetchAdminMerchants: vi.fn(), logError: vi.fn() }));

vi.mock('@/lib/api/admin-merchants', () => ({ fetchAdminMerchants: mocks.fetchAdminMerchants }));
vi.mock('@/lib/logger', () => ({ logger: { error: mocks.logError } }));
vi.mock('next/form', () => ({
  default: ({ children, ...props }: React.ComponentProps<'form'>) => (
    <form {...props}>{children}</form>
  ),
}));
vi.mock('@/app/(admin)/admin/analytics/merchant-clicks-results', () => ({
  MerchantClicksResults: ({ query }: { query: { merchant?: string } }) => (
    <p>results for {query.merchant ?? 'nobody'}</p>
  ),
  MerchantClicksLoading: () => <p>loading</p>,
}));

import {
  CLICK_MEANING_TEXT,
  MerchantClicksSection,
} from '@/app/(admin)/admin/analytics/merchant-clicks-section';
import { parseClicksQuery } from '@/lib/admin-clicks/range';

const NOW = new Date('2026-10-17T10:00:00Z');
const html = (node: React.ReactNode) => renderToStaticMarkup(node as React.ReactElement);
const merchantsPage = (names: string[], total = names.length) => ({
  success: true as const,
  data: { merchants: names.map((name, i) => ({ id: `m${i + 1}`, name })), total_count: total },
});
const parsed = (raw: Record<string, string | string[]> = {}) => parseClicksQuery(raw, NOW);

describe('MerchantClicksSection', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.fetchAdminMerchants.mockResolvedValue(merchantsPage(['Acme', 'Beta & Co']));
  });

  it('has a heading, says what a click is and is not, and labels every control', async () => {
    const markup = html(await MerchantClicksSection({ parsed: parsed() }));

    expect(markup).toContain('aria-labelledby="clicks-heading"');
    expect(markup).toMatch(/<h2[^>]*id="clicks-heading"[^>]*>Clicks by merchant<\/h2>/);
    expect(markup).toContain('It is not a sale, a sign-up or a claim');
    expect(CLICK_MEANING_TEXT).toMatch(/not a sale/);
    expect(CLICK_MEANING_TEXT).toMatch(/unique people/);
    expect(CLICK_MEANING_TEXT).toMatch(/bots/);
    for (const [id, label] of [
      ['clicks-merchant', 'Merchant'],
      ['clicks-from', 'From date'],
      ['clicks-to', 'To date'],
    ]) {
      expect(markup).toContain(`for="${id}">${label}</label>`);
      expect(markup).toMatch(new RegExp(`id="${id}"`));
    }
    expect(markup).toContain('>Show report</button>');
    expect(markup).toContain('Dates are in UTC and both days are included. At most 366 days.');
  });

  it('lists the merchants, escaped, with the chosen one selected, and starts at the last 30 days', async () => {
    const markup = html(await MerchantClicksSection({ parsed: parsed({ merchant: 'm2' }) }));

    expect(markup).toContain('<option value="">Choose a merchant</option>');
    expect(markup).toContain('<option value="m1">Acme</option>');
    expect(markup).toContain('<option value="m2" selected="">Beta &amp; Co</option>');
    expect(markup).toContain('value="2026-09-18"');
    expect(markup).toContain('value="2026-10-17"');
  });

  it('says when only the first merchants are listed', async () => {
    mocks.fetchAdminMerchants.mockResolvedValue(merchantsPage(['Acme'], 250));

    const markup = html(await MerchantClicksSection({ parsed: parsed() }));

    expect(markup).toContain('Only the first 1 merchants by name are listed.');
    expect(markup).toContain('aria-describedby="clicks-merchant-hint"');
  });

  it('hands the report the merchant and the range of the link', async () => {
    const markup = html(
      await MerchantClicksSection({
        parsed: parsed({ merchant: 'm1', from: '2026-03-10', to: '2026-03-11' }),
      })
    );

    expect(markup).toContain('results for m1');
    expect(markup).toContain('value="2026-03-10"');
  });

  it('shows a curated error with a retry link that keeps the range when the merchant list fails', async () => {
    mocks.fetchAdminMerchants.mockResolvedValue({
      success: false,
      error: 'SECRET postgres://x',
      status: 502,
    });

    const markup = html(await MerchantClicksSection({ parsed: parsed() }));

    expect(markup).toContain('role="alert"');
    expect(markup).toContain('Something went wrong. Please try again.');
    expect(markup).not.toContain('SECRET');
    expect(markup).toMatch(/<a[^>]*href="\/admin\/analytics\?from=2026-09-18&amp;to=2026-10-17"/);
    expect(markup).not.toContain('<form');
    expect(mocks.logError).toHaveBeenCalledTimes(1);
  });

  it('does not log a refusal the admin can expect', async () => {
    mocks.fetchAdminMerchants.mockResolvedValue({
      success: false,
      error: 'Forbidden',
      status: 403,
    });

    const markup = html(await MerchantClicksSection({ parsed: parsed() }));

    expect(markup).toContain('You do not have permission to do that');
    expect(mocks.logError).not.toHaveBeenCalled();
  });

  it('marks a rejected field invalid, explains why and never echoes the value', async () => {
    const markup = html(
      await MerchantClicksSection({
        parsed: parsed({ merchant: '<script>x</script>', from: 'soon' }),
      })
    );

    expect(markup).not.toContain('script');
    expect(markup).not.toContain('soon');
    expect(markup).toContain('id="clicks-notice-merchant"');
    expect(markup).toContain('id="clicks-notice-from"');
    expect(markup).toMatch(/id="clicks-merchant"[^>]*aria-invalid="true"/);
    expect(markup).toMatch(
      /id="clicks-from"[^>]*aria-describedby="clicks-date-hint clicks-notice-from"/
    );
    expect(markup).toContain('role="status" id="clicks-notices"');
  });

  it('points both date fields at the range notice when the range is refused', async () => {
    const markup = html(
      await MerchantClicksSection({ parsed: parsed({ from: '2026-03-11', to: '2026-03-10' }) })
    );

    expect(markup).toContain('id="clicks-notice-range"');
    expect(markup).toMatch(
      /id="clicks-to"[^>]*aria-describedby="clicks-date-hint clicks-notice-range"/
    );
  });
});
