// @vitest-environment node
/**
 * The "Clicks by merchant" section: a labelled GET form (merchant picker and a UTC date range), the note on what
 * a click is, the one status region, a curated error when the merchant list fails, a merchant that is not in the
 * first page of the list, a refused range that runs no report, and a rejected URL value that is never echoed.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { Suspense, isValidElement, type ReactElement, type ReactNode } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const mocks = vi.hoisted(() => ({
  fetchAdminMerchants: vi.fn(),
  fetchAdminMerchant: vi.fn(),
  logError: vi.fn(),
}));

vi.mock('@/lib/api/admin-merchants', () => ({
  fetchAdminMerchants: mocks.fetchAdminMerchants,
  fetchAdminMerchant: mocks.fetchAdminMerchant,
}));
vi.mock('@/lib/logger', () => ({ logger: { error: mocks.logError } }));
vi.mock('next/form', () => ({
  default: ({
    children,
    scroll,
    ...props
  }: React.ComponentProps<'form'> & { scroll?: boolean }) => (
    <form data-scroll={String(scroll)} {...props}>
      {children}
    </form>
  ),
}));
vi.mock('@/app/(admin)/admin/analytics/merchant-clicks-results', () => ({
  CLICKS_REPORT_HEADING_ID: 'clicks-report-heading',
  MerchantClicksResults: ({ query }: { query: { merchant?: string } }) => (
    <p>results for {query.merchant ?? 'nobody'}</p>
  ),
  MerchantClicksLoading: () => <p>loading</p>,
}));

import {
  MerchantClicksSection,
  RANGE_REFUSED_TEXT,
} from '@/app/(admin)/admin/analytics/merchant-clicks-section';
import { parseClicksQuery } from '@/lib/admin-clicks/range';

const NOW = new Date('2026-10-17T10:00:00Z');
const html = (node: React.ReactNode) => renderToStaticMarkup(node as React.ReactElement);
const merchantsPage = (names: string[], total = names.length) => ({
  success: true as const,
  data: {
    merchants: names.map((name, i) => ({ id: `m${i + 1}`, name })),
    total_count: total,
    limit: 100,
    offset: 0,
  },
});
const parsed = (raw: Record<string, string | string[]> = {}) => parseClicksQuery(raw, NOW);
const render = async (raw: Record<string, string | string[]> = {}) =>
  html(
    await MerchantClicksSection({
      parsed: parsed(raw),
      merchants: Promise.resolve(mocks.fetchAdminMerchants()),
    })
  );

describe('MerchantClicksSection', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.fetchAdminMerchants.mockResolvedValue(merchantsPage(['Acme', 'Beta & Co']));
    mocks.fetchAdminMerchant.mockResolvedValue({ success: false, error: 'x', status: 404 });
  });

  it('has a plain section heading, says what a click is and is not, and labels every control', async () => {
    const markup = await render();

    expect(markup).toContain('aria-labelledby="clicks-heading"');
    expect(markup).toMatch(/<h2[^>]*id="clicks-heading"[^>]*>Clicks by merchant<\/h2>/);
    expect(markup).not.toMatch(/<h2[^>]*tabindex/);
    expect(markup).toContain('It is not a sale, a sign-up or a claim');
    expect(markup).toContain('unique people');
    expect(markup).toContain('bots');
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

  it('turns off the form’s scroll to the top, so the focused results heading is what the page shows', async () => {
    expect(await render()).toContain('data-scroll="false"');
  });

  it('puts no min or max on the date fields, so a whole range can move in one submit', async () => {
    const markup = await render({ from: '2026-03-10', to: '2026-03-11' });

    expect(markup).toMatch(/<input[^>]*id="clicks-from"[^>]*>/);
    expect(markup.match(/<input[^>]*id="clicks-from"[^>]*>/)?.[0]).not.toMatch(/\smax=/);
    expect(markup.match(/<input[^>]*id="clicks-to"[^>]*>/)?.[0]).not.toMatch(/\smin=/);
  });

  it('requires a merchant', async () => {
    expect(await render()).toMatch(/<select[^>]*id="clicks-merchant"[^>]*required/);
  });

  it('has one status region, empty, before the report and outside it', async () => {
    const markup = await render({ merchant: 'm1' });

    expect(markup).toContain('<p role="status" id="clicks-status" class="sr-only"></p>');
    expect(markup.indexOf('id="clicks-status"')).toBeLessThan(markup.indexOf('results for m1'));
  });

  it('lists the merchants, escaped, with the chosen one selected, and starts at the last 30 days', async () => {
    const markup = await render({ merchant: 'm2' });

    expect(markup).toContain('<option value="">Choose a merchant</option>');
    expect(markup).toContain('<option value="m1">Acme</option>');
    expect(markup).toContain('<option value="m2" selected="">Beta &amp; Co</option>');
    expect(markup).toContain('value="2026-09-18"');
    expect(markup).toContain('value="2026-10-17"');
    expect(mocks.fetchAdminMerchant).not.toHaveBeenCalled();
  });

  it('adds a chosen merchant that the first page of the list lacks, fetched by id and selected', async () => {
    mocks.fetchAdminMerchants.mockResolvedValue(merchantsPage(['Acme'], 250));
    mocks.fetchAdminMerchant.mockResolvedValue({
      success: true,
      data: { id: 'm999', name: 'Far Away Ltd' },
    });

    const markup = await render({ merchant: 'm999' });

    expect(mocks.fetchAdminMerchant).toHaveBeenCalledWith('m999');
    expect(markup).toContain('<option value="m999" selected="">Far Away Ltd</option>');
  });

  it('keeps the placeholder when the extra merchant cannot be fetched', async () => {
    mocks.fetchAdminMerchants.mockResolvedValue(merchantsPage(['Acme'], 250));

    const markup = await render({ merchant: 'm999' });

    expect(markup).not.toContain('value="m999"');
    expect(markup).toContain('results for m999');
  });

  it('says when only the first merchants are listed', async () => {
    mocks.fetchAdminMerchants.mockResolvedValue(merchantsPage(['Acme'], 250));

    const markup = await render();

    expect(markup).toContain('Only the first 1 merchants by name are listed.');
    expect(markup).toContain('aria-describedby="clicks-merchant-hint"');
  });

  it('has the results heading after the form, as an h3 that can take focus', async () => {
    const markup = await render({ merchant: 'm1' });

    expect(markup).toMatch(
      /<h3[^>]*id="clicks-report-heading"[^>]*tabindex="-1"[^>]*>Report<\/h3>/
    );
    expect(markup.indexOf('</form>')).toBeLessThan(markup.indexOf('clicks-report-heading'));
    expect(markup.indexOf('clicks-report-heading')).toBeLessThan(markup.indexOf('results for m1'));
  });

  it('hands the report the merchant and the range of the link', async () => {
    const markup = await render({ merchant: 'm1', from: '2026-03-10', to: '2026-03-11' });

    expect(markup).toContain('results for m1');
    expect(markup).toContain('value="2026-03-10"');
    expect(markup).not.toContain(RANGE_REFUSED_TEXT);
  });

  it.each([
    [{ merchant: 'm1', from: '2026-03-11', to: '2026-03-10' }],
    [{ merchant: 'm1', from: '2025-01-01', to: '2026-01-02' }],
    [{ merchant: 'm1', from: 'soon' }],
  ])('runs no report and shows the notice when the dates are refused: %j', async (raw) => {
    const markup = await render(raw);

    expect(markup).not.toContain('results for');
    expect(markup).not.toContain('Download CSV');
    expect(markup).toContain(RANGE_REFUSED_TEXT);
    expect(markup).toMatch(/id="clicks-notice-(range|from)"/);
    expect(markup).toContain('value="2026-09-18"');
  });

  it('shows a curated error with a retry link that keeps the range when the merchant list fails', async () => {
    mocks.fetchAdminMerchants.mockResolvedValue({
      success: false,
      error: 'SECRET postgres://x',
      status: 502,
    });

    const markup = await render();

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

    const markup = await render();

    expect(markup).toContain('You do not have permission to do that');
    expect(mocks.logError).not.toHaveBeenCalled();
  });

  it('marks a rejected field invalid, explains why and never echoes the value', async () => {
    const markup = await render({ merchant: '<script>x</script>', from: 'soon' });

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
    const markup = await render({ from: '2026-03-11', to: '2026-03-10' });

    expect(markup).toContain('id="clicks-notice-range"');
    expect(markup).toMatch(
      /id="clicks-to"[^>]*aria-describedby="clicks-date-hint clicks-notice-range"/
    );
  });

  it('keys the streamed report by the link, so a new merchant or range shows the loading line again', async () => {
    const find = (node: ReactNode): ReactElement | undefined => {
      if (Array.isArray(node)) return node.map(find).find(Boolean);
      if (!isValidElement(node)) return undefined;
      if (node.type === Suspense) return node;
      return find((node.props as { children?: ReactNode }).children);
    };
    const tree = (raw: Record<string, string>) =>
      MerchantClicksSection({
        parsed: parsed(raw),
        merchants: Promise.resolve(merchantsPage(['Acme']) as never),
      });

    const first = find(await tree({ merchant: 'm1', from: '2026-03-10', to: '2026-03-11' }));
    const second = find(await tree({ merchant: 'm1', from: '2026-03-12', to: '2026-03-13' }));

    expect(first?.key).toBe('/admin/analytics?merchant=m1&from=2026-03-10&to=2026-03-11');
    expect(second?.key).not.toBe(first?.key);
  });
});
