/**
 * The click report's date range and URL parameters: real UTC days only, from before or on to, at most 366 days,
 * and a rejected value is dropped with a fixed notice and never echoed.
 */
import { describe, expect, it } from 'vitest';
import {
  addDays,
  clickRangeError,
  clicksHref,
  clicksNoticeId,
  clicksNoticeItemId,
  daysInclusive,
  defaultClickRange,
  exportClicksInputSchema,
  MAX_CLICK_RANGE_DAYS,
  parseClicksQuery,
} from '@/lib/admin-clicks/range';

const NOW = new Date('2026-10-17T23:59:59Z');

describe('daysInclusive and addDays', () => {
  it('counts both ends', () => {
    expect(daysInclusive('2026-03-10', '2026-03-10')).toBe(1);
    expect(daysInclusive('2026-03-10', '2026-03-31')).toBe(22);
    expect(daysInclusive('2025-01-01', '2026-01-01')).toBe(MAX_CLICK_RANGE_DAYS);
  });

  it('adds days across month and year ends', () => {
    expect(addDays('2026-12-31', 1)).toBe('2027-01-01');
    expect(addDays('2026-03-01', -1)).toBe('2026-02-28');
  });
});

describe('defaultClickRange', () => {
  it('is the last 30 UTC days ending today', () => {
    expect(defaultClickRange(NOW)).toEqual({ from: '2026-09-18', to: '2026-10-17' });
  });
});

describe('clickRangeError', () => {
  it('accepts a single day and exactly 366 days', () => {
    expect(clickRangeError('2026-03-10', '2026-03-10')).toBeUndefined();
    expect(clickRangeError('2025-01-01', '2026-01-01')).toBeUndefined();
  });

  it('refuses a reversed range and one over 366 days', () => {
    expect(clickRangeError('2026-03-11', '2026-03-10')).toMatch(/on or before/);
    expect(clickRangeError('2025-01-01', '2026-01-02')).toMatch(/366 days/);
  });
});

describe('parseClicksQuery', () => {
  it('uses the default range when no dates are given', () => {
    const parsed = parseClicksQuery({}, NOW);

    expect(parsed.query).toEqual({ merchant: undefined, from: '2026-09-18', to: '2026-10-17' });
    expect(parsed.notices).toEqual([]);
  });

  it('takes a valid merchant and range as they are', () => {
    const parsed = parseClicksQuery(
      { merchant: 'abc_123-X', from: '2026-03-10', to: '2026-03-11' },
      NOW
    );

    expect(parsed.query).toEqual({ merchant: 'abc_123-X', from: '2026-03-10', to: '2026-03-11' });
    expect(parsed.invalid.size).toBe(0);
  });

  it('treats blank values as not set', () => {
    const parsed = parseClicksQuery({ merchant: ' ', from: '', to: '' }, NOW);

    expect(parsed.query.merchant).toBeUndefined();
    expect(parsed.notices).toEqual([]);
  });

  it.each([
    [{ merchant: 'bad id!' }, 'merchant'],
    [{ merchant: ['a', 'b'] }, 'merchant'],
    [{ merchant: 'x'.repeat(129) }, 'merchant'],
    [{ from: '10/03/2026' }, 'from'],
    [{ from: '2026-02-30' }, 'from'],
    [{ to: 'tomorrow' }, 'to'],
    [{ to: ['2026-03-10', '2026-03-11'] }, 'to'],
  ])('drops %j with a notice and marks %s invalid', (raw, field) => {
    const parsed = parseClicksQuery(raw, NOW);

    expect(parsed.invalid.has(field as 'merchant' | 'from' | 'to')).toBe(true);
    expect(parsed.notices).toHaveLength(1);
    expect(JSON.stringify(parsed)).not.toContain('bad id');
    expect(JSON.stringify(parsed)).not.toContain('tomorrow');
  });

  it('falls back to the default range when the range is reversed or too long', () => {
    for (const raw of [
      { from: '2026-03-11', to: '2026-03-10' },
      { from: '2025-01-01', to: '2026-01-02' },
    ]) {
      const parsed = parseClicksQuery(raw, NOW);

      expect(parsed.query).toMatchObject({ from: '2026-09-18', to: '2026-10-17' });
      expect(parsed.notices).toHaveLength(1);
      expect([...parsed.invalid].sort()).toEqual(['from', 'to']);
    }
  });

  it('fills the missing end from the other one within the limit', () => {
    expect(parseClicksQuery({ from: '2026-10-01' }, NOW).query).toMatchObject({
      from: '2026-10-01',
      to: '2026-10-17',
    });
    expect(parseClicksQuery({ to: '2026-10-05' }, NOW).query).toMatchObject({
      from: '2026-09-06',
      to: '2026-10-05',
    });
  });
});

describe('exportClicksInputSchema', () => {
  const good = { merchantId: 'abc123', from: '2026-03-10', to: '2026-03-11' };

  it('accepts a merchant id and a valid range', () => {
    expect(exportClicksInputSchema.safeParse(good).success).toBe(true);
  });

  it.each([
    [{ ...good, merchantId: '' }],
    [{ ...good, merchantId: 'a/b' }],
    [{ ...good, merchantId: '../x' }],
    [{ ...good, from: '2026-02-30' }],
    [{ ...good, from: '2026-03-12' }],
    [{ ...good, from: '2025-01-01', to: '2026-01-02' }],
    [{ ...good, extra: 1 }],
    [null],
    ['text'],
  ])('refuses %j', (input) => {
    expect(exportClicksInputSchema.safeParse(input).success).toBe(false);
  });
});

describe('clicksHref', () => {
  it('carries the merchant and the range', () => {
    expect(clicksHref({ merchant: 'm1', from: '2026-03-10', to: '2026-03-11' })).toBe(
      '/admin/analytics?merchant=m1&from=2026-03-10&to=2026-03-11'
    );
  });

  it('leaves the merchant out when none is chosen', () => {
    expect(clicksHref({ merchant: undefined, from: '2026-03-10', to: '2026-03-11' })).toBe(
      '/admin/analytics?from=2026-03-10&to=2026-03-11'
    );
  });
});

describe('notice ids', () => {
  it('ties a rejected field to its own notice and a refused range to the range notice', () => {
    const merchant = parseClicksQuery({ merchant: '!' }, NOW);
    const range = parseClicksQuery({ from: '2026-03-11', to: '2026-03-10' }, NOW);

    expect(clicksNoticeId('merchant', merchant)).toBe('clicks-notice-merchant');
    expect(clicksNoticeId('from', merchant)).toBeUndefined();
    expect(clicksNoticeId('to', range)).toBe('clicks-notice-range');
    expect(clicksNoticeItemId(merchant.notices[0])).toBe('clicks-notice-merchant');
    expect(clicksNoticeItemId(range.notices[0])).toBe('clicks-notice-range');
    expect(clicksNoticeItemId('something else')).toBe('clicks-notice-other');
  });
});
