// @vitest-environment node
/**
 * The merchant click CSV action: the admin gate runs first (a refusal calls nothing), input is validated, the file
 * is built from the API report, and every failure is a curated message that never carries backend text.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

const SECRET = 'SECRET-sk_live_abc123 postgres://user:pw@host/db';

const mocks = vi.hoisted(() => ({
  requireAdminSession: vi.fn(),
  fetchMerchantClicks: vi.fn(),
  logError: vi.fn(),
}));

vi.mock('@/lib/api/admin-guard', () => ({ requireAdminSession: mocks.requireAdminSession }));
vi.mock('@/lib/api/admin-analytics', () => ({ fetchMerchantClicks: mocks.fetchMerchantClicks }));
vi.mock('@/lib/logger', () => ({
  logger: { info: vi.fn(), warn: vi.fn(), error: mocks.logError },
}));

import { exportMerchantClicksAction } from '@/app/actions/admin/merchant-clicks-actions';
import { CSV_TOO_LARGE_MESSAGE } from '@/lib/admin-clicks/csv';
import {
  ADMIN_FORBIDDEN_MESSAGE,
  ADMIN_REQUEST_FAILED_MESSAGE,
  ADMIN_UNAVAILABLE_MESSAGE,
} from '@/lib/api/admin-errors';
import {
  CLICKS_MERCHANT_NOT_FOUND_MESSAGE,
  INVALID_FORM_MESSAGE,
} from '@/lib/api/admin-catalogue-errors';

const INPUT = { merchantId: 'm1', from: '2026-03-10', to: '2026-03-11' };
const REPORT = {
  merchant: { id: 'm1', name: 'Acme', slug: 'acme' },
  from_date: '2026-03-10',
  to_date: '2026-03-11',
  total_clicks: 3,
  offers_truncated: false,
  offers: [
    {
      offer_id: 'o1',
      slug: 'soap',
      title: '=HYPERLINK("x")',
      total_clicks: 3,
      days: [{ date: '2026-03-10', clicks: 3 }],
    },
  ],
};
const fail = (status: number | undefined, code?: string) => ({
  success: false as const,
  error: SECRET,
  status,
  code,
});

describe('exportMerchantClicksAction', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.requireAdminSession.mockResolvedValue({ ok: true });
    mocks.fetchMerchantClicks.mockResolvedValue({ success: true, data: REPORT });
  });

  it('returns the file name and the CSV built from the report', async () => {
    const result = await exportMerchantClicksAction(INPUT);

    expect(mocks.fetchMerchantClicks).toHaveBeenCalledWith(INPUT);
    expect(result).toEqual({
      success: true,
      data: {
        filename: 'freejoy-clicks-acme-2026-03-10-to-2026-03-11.csv',
        csv:
          String.fromCharCode(0xfeff) +
          'Merchant,Offer title,Offer slug,Date (UTC),Clicks\r\n' +
          'Acme,"\'=HYPERLINK(""x"")",soap,2026-03-10,3\r\n',
        truncated: false,
      },
    });
  });

  it('refuses a file over the size limit with a curated message and sends nothing', async () => {
    const days = Array.from({ length: 366 }, (_, i) => ({
      date: new Date(Date.UTC(2025, 0, 1 + i)).toISOString().slice(0, 10),
      clicks: 1,
    }));
    const big = Array.from({ length: 100 }, (_, i) => ({
      offer_id: `o${i}`,
      slug: `slug-${i}`,
      title: `${'T'.repeat(120)} ${i}`,
      total_clicks: 366,
      days,
    }));
    mocks.fetchMerchantClicks.mockResolvedValue({
      success: true,
      data: { ...REPORT, offers: big },
    });

    const result = await exportMerchantClicksAction({
      merchantId: 'm1',
      from: '2025-01-01',
      to: '2026-01-01',
    });

    expect(result).toEqual({ success: false, error: CSV_TOO_LARGE_MESSAGE });
  });

  it('builds the file name from the validated dates, not from the API answer', async () => {
    mocks.fetchMerchantClicks.mockResolvedValue({
      success: true,
      data: {
        ...REPORT,
        merchant: { ...REPORT.merchant, slug: '../EVIL slug' },
        from_date: 'x/y',
        to_date: '..',
      },
    });

    const result = await exportMerchantClicksAction(INPUT);

    expect(result).toMatchObject({
      success: true,
      data: { filename: 'freejoy-clicks-evil-slug-2026-03-10-to-2026-03-11.csv' },
    });
  });

  it('passes on that the report was cut at the offer limit', async () => {
    mocks.fetchMerchantClicks.mockResolvedValue({
      success: true,
      data: { ...REPORT, offers_truncated: true },
    });

    const result = await exportMerchantClicksAction(INPUT);

    expect(result).toMatchObject({ success: true, data: { truncated: true } });
  });

  it.each([
    ['no session', { ok: false, reason: 'no-session', error: ADMIN_FORBIDDEN_MESSAGE }],
    ['not an admin', { ok: false, reason: 'not-admin', error: ADMIN_FORBIDDEN_MESSAGE }],
    ['an outage', { ok: false, reason: 'unavailable', error: ADMIN_UNAVAILABLE_MESSAGE }],
  ])('refuses %s without calling the API', async (_name, gate) => {
    mocks.requireAdminSession.mockResolvedValue(gate);

    const result = await exportMerchantClicksAction(INPUT);

    expect(result).toEqual({ success: false, error: gate.error });
    expect(mocks.fetchMerchantClicks).not.toHaveBeenCalled();
  });

  it.each([
    [{ ...INPUT, merchantId: '../etc' }],
    [{ ...INPUT, from: '2026-03-12' }],
    [{ ...INPUT, from: '2025-01-01', to: '2026-01-02' }],
    [{ ...INPUT, to: 'not a date' }],
    [{ merchantId: 'm1' }],
    [undefined],
  ])('refuses %j before calling the API and says which field', async (input) => {
    const result = await exportMerchantClicksAction(input);

    expect(result).toMatchObject({ success: false, error: INVALID_FORM_MESSAGE });
    expect(mocks.fetchMerchantClicks).not.toHaveBeenCalled();
  });

  it('names the end date when the range is too long', async () => {
    const result = await exportMerchantClicksAction({
      ...INPUT,
      from: '2025-01-01',
      to: '2026-01-02',
    });

    expect(result).toMatchObject({ fieldErrors: { to: expect.stringContaining('366 days') } });
  });

  it.each([
    [404, 'not_found', CLICKS_MERCHANT_NOT_FOUND_MESSAGE, false],
    [403, undefined, ADMIN_FORBIDDEN_MESSAGE, false],
    [401, undefined, ADMIN_FORBIDDEN_MESSAGE, false],
    [422, 'invalid_range', expect.stringContaining('366 days'), false],
    [429, undefined, expect.stringContaining('Too many requests'), false],
    [502, undefined, ADMIN_REQUEST_FAILED_MESSAGE, true],
    [undefined, undefined, ADMIN_UNAVAILABLE_MESSAGE, true],
  ])('status %s gives a curated message (logged: %s)', async (status, code, message, logged) => {
    mocks.fetchMerchantClicks.mockResolvedValue(fail(status, code));

    const result = await exportMerchantClicksAction(INPUT);

    expect(result).toEqual({ success: false, error: message });
    expect(JSON.stringify(result)).not.toContain('SECRET');
    expect(mocks.logError).toHaveBeenCalledTimes(logged ? 1 : 0);
  });
});
