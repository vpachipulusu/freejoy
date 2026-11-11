// @vitest-environment node
/**
 * The click report for one merchant: the states (choose, error, no offers, no clicks, result), the table that stays
 * usable for a long range (a daily breakdown behind a disclosure, never one column per day), and the notes for
 * retention, the offer limit and bots.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';

const mocks = vi.hoisted(() => ({ fetchMerchantClicks: vi.fn(), logError: vi.fn() }));

vi.mock('@/lib/api/admin-analytics', () => ({ fetchMerchantClicks: mocks.fetchMerchantClicks }));
vi.mock('@/lib/logger', () => ({ logger: { error: mocks.logError } }));
vi.mock('next/navigation', () => ({ useRouter: () => ({ refresh: vi.fn() }) }));
vi.mock('@/app/actions/admin/merchant-clicks-actions', () => ({
  exportMerchantClicksAction: vi.fn(),
}));

import {
  CHOOSE_MERCHANT_TEXT,
  MerchantClicksResults,
  NO_OFFERS_TEXT,
} from '@/app/(admin)/admin/analytics/merchant-clicks-results';

const html = (node: React.ReactNode) => renderToStaticMarkup(node as React.ReactElement);
const QUERY = { merchant: 'm1', from: '2026-03-10', to: '2026-03-31' };

function offer(title: string, days: [string, number][], id = title.toLowerCase()) {
  return {
    offer_id: id,
    slug: id,
    title,
    total_clicks: days.reduce((sum, [, clicks]) => sum + clicks, 0),
    days: days.map(([date, clicks]) => ({ date, clicks })),
  };
}

function report(overrides: Record<string, unknown> = {}) {
  const offers = [
    offer('Soap <b>sample</b>', [
      ['2026-03-10', 2],
      ['2026-03-11', 1],
    ]),
    offer('Balm', [['2026-03-12', 1200]]),
    offer('Quiet', []),
  ];
  return {
    merchant: { id: 'm1', name: 'Acme', slug: 'acme' },
    from_date: '2026-03-10',
    to_date: '2026-03-31',
    total_clicks: 1203,
    excluded_bot_clicks: 0,
    offers_total: 3,
    max_offers: 100,
    offers_truncated: false,
    offers,
    retention: { days: 395, earliest_complete_date: '2025-03-18', starts_before_retention: false },
    basis: { definition: 'x', bot_rule: 'y' },
    ...overrides,
  };
}

describe('MerchantClicksResults', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.fetchMerchantClicks.mockResolvedValue({ success: true, data: report() });
  });

  it('asks for a merchant and calls nothing when none is chosen', async () => {
    const markup = html(await MerchantClicksResults({ query: { ...QUERY, merchant: undefined } }));

    expect(markup).toContain(CHOOSE_MERCHANT_TEXT);
    expect(mocks.fetchMerchantClicks).not.toHaveBeenCalled();
  });

  it('asks the API for the merchant and the range of the link', async () => {
    await MerchantClicksResults({ query: QUERY });

    expect(mocks.fetchMerchantClicks).toHaveBeenCalledWith({
      merchantId: 'm1',
      from: '2026-03-10',
      to: '2026-03-31',
    });
  });

  it('summarises the range and shows a captioned table with header cells', async () => {
    const markup = html(await MerchantClicksResults({ query: QUERY }));

    expect(markup).toContain('Acme: 1,203 clicks from 10 Mar 2026 to 31 Mar 2026 (22 days, UTC).');
    expect(markup).toMatch(/<p class="[^"]*">Acme: 1,203 clicks/);
    expect(markup).toMatch(
      /<caption[^>]*>Clicks per offer for Acme, 10 Mar 2026 to 31 Mar 2026, in UTC days<\/caption>/
    );
    for (const heading of ['Offer', 'Total clicks', 'Daily breakdown']) {
      expect(markup).toMatch(new RegExp(`<th[^>]*scope="col"[^>]*>${heading}</th>`));
    }
    expect(markup).toContain('>1,200<');
    expect(markup).toContain('All offers');
    expect(markup).toMatch(/<th[^>]*scope="row"[^>]*>Soap &lt;b&gt;sample&lt;\/b&gt;<\/th>/);
    expect(markup).toMatch(/<tfoot[\s\S]*<th[^>]*scope="row"[^>]*>All offers<\/th>/);
    expect(markup).toMatch(/<td[^>]*colSpan="2"[^>]*>1,203<\/td>/);
  });

  it('escapes an offer title and shows no markup from it', async () => {
    const markup = html(await MerchantClicksResults({ query: QUERY }));

    expect(markup).toContain('Soap &lt;b&gt;sample&lt;/b&gt;');
    expect(markup).not.toContain('<b>sample');
  });

  it('keeps a long range usable: one row per offer and a disclosure for the days, not a column per day', async () => {
    const days = Array.from({ length: 366 }, (_, i): [string, number] => [
      new Date(Date.UTC(2025, 0, 1 + i)).toISOString().slice(0, 10),
      1,
    ]);
    mocks.fetchMerchantClicks.mockResolvedValue({
      success: true,
      data: report({
        from_date: '2025-01-01',
        to_date: '2026-01-01',
        total_clicks: 366,
        offers: [offer('Busy', days)],
      }),
    });

    const markup = html(await MerchantClicksResults({ query: QUERY }));

    expect(markup.match(/<th /g)?.length).toBeLessThanOrEqual(8);
    expect(markup).toContain('366 days with clicks');
    expect(markup).toContain('<details');
    expect(markup).toContain('role="region"');
    expect(markup).toContain('aria-label="Daily clicks for Busy"');
    expect(markup).toContain('tabindex="0"');
    expect(markup).toContain('<time dateTime="2026-01-01">1 Jan 2026</time>');
    expect(markup).not.toContain('<time dateTime="2025-01-01">');
  });

  it('says "No clicks" for an offer without days and "1 day" for one day', async () => {
    const markup = html(await MerchantClicksResults({ query: QUERY }));

    expect(markup).toContain('No clicks');
    expect(markup).toContain('2 days with clicks');
    expect(markup).toContain('1 day with clicks');
  });

  it('says so plainly when there were no clicks at all, and still lists the offers', async () => {
    mocks.fetchMerchantClicks.mockResolvedValue({
      success: true,
      data: report({ total_clicks: 0, offers: [offer('Quiet', [])] }),
    });

    const markup = html(await MerchantClicksResults({ query: QUERY }));

    expect(markup).toContain('0 clicks');
    expect(markup).toContain('No clicks were recorded in this range.');
    expect(markup).toContain('Quiet');
    expect(markup).toContain('Download CSV');
  });

  it('shows an empty state and no download when the merchant has no offers', async () => {
    mocks.fetchMerchantClicks.mockResolvedValue({
      success: true,
      data: report({ offers: [], offers_total: 0, total_clicks: 0 }),
    });

    const markup = html(await MerchantClicksResults({ query: QUERY }));

    expect(markup).toContain(NO_OFFERS_TEXT);
    expect(markup).not.toContain('Download CSV');
    expect(markup).not.toContain('<table');
  });

  it('warns when the range starts before the data does, with the first complete day', async () => {
    mocks.fetchMerchantClicks.mockResolvedValue({
      success: true,
      data: report({
        retention: {
          days: 395,
          earliest_complete_date: '2025-03-18',
          starts_before_retention: true,
        },
      }),
    });

    const markup = html(await MerchantClicksResults({ query: QUERY }));

    expect(markup).toContain('Click records are kept for 395 days');
    expect(markup).toContain('Clicks before 18 Mar 2025 are missing');
    expect(markup).toContain('too low');
  });

  it('says nothing about retention when the range is inside the data', async () => {
    const markup = html(await MerchantClicksResults({ query: QUERY }));

    expect(markup).not.toContain('Click records are kept');
  });

  it('says when the offer limit cut the list and what the total and the file cover', async () => {
    mocks.fetchMerchantClicks.mockResolvedValue({
      success: true,
      data: report({ offers_truncated: true, offers_total: 140, max_offers: 100 }),
    });

    const markup = html(await MerchantClicksResults({ query: QUERY }));

    expect(markup).toContain('Only the 100 offers with the most clicks are listed, out of 140.');
    expect(markup).toContain(
      'The total above and at the bottom counts every offer of this merchant. ' +
        'The table and the CSV file list only these 100, so the file adds up to less.'
    );
    expect(markup).toContain('All offers of this merchant');
  });

  it('reports bots left out, and says nothing when there were none', async () => {
    mocks.fetchMerchantClicks.mockResolvedValue({
      success: true,
      data: report({ excluded_bot_clicks: 1500 }),
    });

    const markup = html(await MerchantClicksResults({ query: QUERY }));

    expect(markup).toContain('1,500 clicks from automated visitors (bots) were left out');
    expect(markup).toContain('This is a minimum');
  });

  it('says nothing about bots when none were left out', async () => {
    const markup = html(await MerchantClicksResults({ query: QUERY }));

    expect(markup).not.toContain('automated visitors');
  });

  it('offers the download for exactly this merchant and range', async () => {
    const markup = html(await MerchantClicksResults({ query: QUERY }));

    expect(markup).toContain('Download CSV');
  });

  it.each([
    [404, undefined, 'That merchant no longer exists. Choose another.', false],
    [403, undefined, 'You do not have permission to do that', false],
    [429, undefined, 'Too many requests. Wait a minute and try again.', false],
    [
      422,
      'invalid_range',
      'Choose dates where the start is on or before the end, at most 366 days apart.',
      false,
    ],
    [502, undefined, 'Something went wrong. Please try again.', true],
    [undefined, undefined, 'The admin service is unavailable. Please try again later.', true],
  ])(
    'status %s (code %s) shows the same curated message as the export action (logged: %s)',
    async (status, code, message, logged) => {
      mocks.fetchMerchantClicks.mockResolvedValue({
        success: false,
        error: 'SECRET select * from clicks',
        status,
        code,
      });

      const markup = html(await MerchantClicksResults({ query: QUERY }));

      expect(markup).toContain('role="alert"');
      expect(markup).toContain(message);
      expect(markup).not.toContain('SECRET');
      expect(markup).toContain('>Retry</button>');
      expect(markup).not.toContain('Download CSV');
      expect(mocks.logError).toHaveBeenCalledTimes(logged ? 1 : 0);
    }
  );

  it('lists at most 31 days per offer and says how many are not shown', async () => {
    const days = Array.from({ length: 40 }, (_, i): [string, number] => [
      new Date(Date.UTC(2026, 0, 1 + i)).toISOString().slice(0, 10),
      1,
    ]);
    mocks.fetchMerchantClicks.mockResolvedValue({
      success: true,
      data: report({ offers: [offer('Busy', days)], total_clicks: 40 }),
    });

    const markup = html(await MerchantClicksResults({ query: QUERY }));

    expect(markup).toContain('40 days with clicks');
    expect(markup).toContain('Showing the 31 most recent days with clicks.');
    expect(markup).toContain('9 earlier days are not shown; the CSV file has every day.');
    expect(markup).toContain('<time dateTime="2026-02-09">');
    expect(markup).not.toContain('<time dateTime="2026-01-09">');
    expect(markup).toContain('>40<');
  });

  it('does not mention hidden days when every day fits', async () => {
    const markup = html(await MerchantClicksResults({ query: QUERY }));

    expect(markup).not.toContain('not shown');
  });

  it('keeps the page light for 100 offers over 366 days: a bounded number of rows', async () => {
    const days = Array.from({ length: 366 }, (_, i): [string, number] => [
      new Date(Date.UTC(2025, 0, 1 + i)).toISOString().slice(0, 10),
      1,
    ]);
    const offers = Array.from({ length: 100 }, (_, i) => offer(`Offer ${i}`, days, `o${i}`));
    mocks.fetchMerchantClicks.mockResolvedValue({
      success: true,
      data: report({
        from_date: '2025-01-01',
        to_date: '2026-01-01',
        offers,
        total_clicks: 36600,
        offers_total: 100,
      }),
    });

    const markup = html(await MerchantClicksResults({ query: QUERY }));

    // per offer: 31 day rows and a header row, plus 100 offer rows, the table header and the footer
    expect(markup.match(/<tr[ >]/g)?.length).toBeLessThan(100 * 32 + 102 + 1);
    expect(markup.match(/<time /g)).toHaveLength(100 * 31);
    expect(markup).toContain('335 earlier days are not shown');
    expect(markup).toContain('366 days with clicks');
  });

  it('names the offer inside each disclosure so the toggles are told apart', async () => {
    const markup = html(await MerchantClicksResults({ query: QUERY }));

    expect(markup).toMatch(
      /<summary[^>]*>2 days with clicks<span class="sr-only">, Soap &lt;b&gt;sample&lt;\/b&gt;<\/span><\/summary>/
    );
  });
});
