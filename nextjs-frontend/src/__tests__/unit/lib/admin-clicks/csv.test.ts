/**
 * The merchant click report as CSV: comma separated, UTF-8 with a byte-order mark, CRLF line endings, quoting
 * per RFC 4180 and no cell a spreadsheet could run as a formula.
 */
import { describe, expect, it } from 'vitest';
import {
  buildClicksCsv,
  clicksCsvFilename,
  csvCell,
  CSV_HEADER,
  type ClicksCsvReport,
} from '@/lib/admin-clicks/csv';

const BOM = String.fromCharCode(0xfeff);

function report(offers: ClicksCsvReport['offers'], merchant = { name: 'Acme', slug: 'acme' }) {
  return { merchant, from_date: '2026-03-10', to_date: '2026-03-11', offers };
}

function offer(title: string, days: [string, number][], slug = 'soap') {
  return { slug, title, days: days.map(([date, clicks]) => ({ date, clicks })) };
}

describe('csvCell', () => {
  it('leaves plain text alone', () => {
    expect(csvCell('Free soap sample')).toBe('Free soap sample');
  });

  it.each([
    ['a,b', '"a,b"'],
    ['say "hi"', '"say ""hi"""'],
    ['line\nbreak', '"line\nbreak"'],
    ['line\rbreak', '"line\rbreak"'],
    ['two\r\nlines', '"two\r\nlines"'],
  ])('quotes %j when it holds a comma, quote, CR or LF', (input, expected) => {
    expect(csvCell(input)).toBe(expected);
  });

  it.each(['=', '+', '-', '@', '\t', '\r'])(
    'prefixes a single quote when a cell starts with %j',
    (leading) => {
      const cell = csvCell(`${leading}1+1`);
      // Unquote to check the cell content as a spreadsheet would read it.
      const content = cell.startsWith('"') ? cell.slice(1, -1).replaceAll('""', '"') : cell;
      expect(content).toBe(`'${leading}1+1`);
    }
  );

  it('neutralises a formula and still quotes it when it holds quotes and commas', () => {
    expect(csvCell('=HYPERLINK("x")')).toBe('"\'=HYPERLINK(""x"")"');
    expect(csvCell('=SUM(A1,A2)')).toBe('"\'=SUM(A1,A2)"');
  });

  it('only looks at the first character', () => {
    expect(csvCell('a=b')).toBe('a=b');
    expect(csvCell('1-2')).toBe('1-2');
    expect(csvCell(' =x')).toBe(' =x');
  });

  it('writes counts as plain numbers, never as text', () => {
    expect(csvCell(0)).toBe('0');
    expect(csvCell(1234)).toBe('1234');
  });

  it('keeps unicode text as it is', () => {
    expect(csvCell('Café crème – 50% off ☕ 日本語')).toBe('Café crème – 50% off ☕ 日本語');
  });
});

describe('buildClicksCsv', () => {
  it('starts with a byte-order mark and the header, and every line ends in CRLF', () => {
    const csv = buildClicksCsv(report([offer('Soap', [['2026-03-10', 2]])]));

    expect(csv.startsWith(BOM)).toBe(true);
    expect(csv.slice(BOM.length)).toBe(
      `${CSV_HEADER.join(',')}\r\nAcme,Soap,soap,2026-03-10,2\r\n`
    );
    expect(csv.replaceAll('\r\n', '')).not.toMatch(/[\r\n]/);
  });

  it('is exactly the UTF-8 bytes EF BB BF followed by the text', () => {
    const bytes = new TextEncoder().encode(buildClicksCsv(report([])));

    expect([...bytes.slice(0, 3)]).toEqual([0xef, 0xbb, 0xbf]);
  });

  it('writes one row per offer per day with clicks, in the order given', () => {
    const csv = buildClicksCsv(
      report([
        offer('Soap', [
          ['2026-03-10', 2],
          ['2026-03-11', 1],
        ]),
        offer('Balm', [['2026-03-11', 5]], 'balm'),
      ])
    );

    expect(csv.slice(BOM.length).split('\r\n')).toEqual([
      CSV_HEADER.join(','),
      'Acme,Soap,soap,2026-03-10,2',
      'Acme,Soap,soap,2026-03-11,1',
      'Acme,Balm,balm,2026-03-11,5',
      '',
    ]);
  });

  it('lists an offer with no clicks once, with an empty date and 0 clicks', () => {
    const csv = buildClicksCsv(report([offer('Quiet', [], 'quiet')]));

    expect(csv.slice(BOM.length).split('\r\n')[1]).toBe('Acme,Quiet,quiet,,0');
  });

  it('is just the header when the merchant has no offers', () => {
    expect(buildClicksCsv(report([]))).toBe(`${BOM}${CSV_HEADER.join(',')}\r\n`);
  });

  it('keeps one value per column for a hostile title, a comma and a quote', () => {
    const csv = buildClicksCsv(
      report(
        [
          offer('=HYPERLINK("x")', [['2026-03-10', 1]], 'evil'),
          offer('Soap, 2 pack', [['2026-03-10', 2]], 'soap'),
          offer('The "best" balm', [['2026-03-10', 3]], 'balm'),
        ],
        { name: '+Acme, Ltd', slug: 'acme' }
      )
    );

    expect(csv.slice(BOM.length).split('\r\n').slice(1, 4)).toEqual([
      '"\'+Acme, Ltd","\'=HYPERLINK(""x"")",evil,2026-03-10,1',
      '"\'+Acme, Ltd","Soap, 2 pack",soap,2026-03-10,2',
      '"\'+Acme, Ltd","The ""best"" balm",balm,2026-03-10,3',
    ]);
  });
});

describe('clicksCsvFilename', () => {
  it('is built from the merchant slug and the dates', () => {
    expect(clicksCsvFilename('acme', '2026-03-10', '2026-03-31')).toBe(
      'freejoy-clicks-acme-2026-03-10-to-2026-03-31.csv'
    );
  });

  it.each([
    ['../../etc/passwd', 'etc-passwd'],
    ['Acme Ltd!', 'acme-ltd'],
    ['a/b\\c:d*e?f"g<h>i|j', 'a-b-c-d-e-f-g-h-i-j'],
    ['---', 'merchant'],
    ['', 'merchant'],
    ['café', 'caf'],
  ])('sanitises the slug %j to %j', (slug, safe) => {
    expect(clicksCsvFilename(slug, '2026-03-10', '2026-03-11')).toBe(
      `freejoy-clicks-${safe}-2026-03-10-to-2026-03-11.csv`
    );
  });

  it('caps the slug length', () => {
    const name = clicksCsvFilename('a'.repeat(300), '2026-03-10', '2026-03-11');

    expect(name.length).toBeLessThan(120);
    expect(name).toMatch(/^freejoy-clicks-a+-2026-03-10-to-2026-03-11\.csv$/);
  });
});
