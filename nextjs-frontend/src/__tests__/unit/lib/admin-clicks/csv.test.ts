/**
 * The merchant click report as CSV: comma separated, UTF-8 with a byte-order mark, CRLF line endings, quoting
 * per RFC 4180 and no cell a spreadsheet could run as a formula.
 */
import { describe, expect, it } from 'vitest';
import {
  buildClicksCsv,
  clicksCsvFilename,
  csvByteLength,
  csvCell,
  CSV_TOO_LARGE_MESSAGE,
  MAX_CSV_BYTES,
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

  it('leaves a formula character in the middle of a word alone', () => {
    expect(csvCell('a=b')).toBe('a=b');
    expect(csvCell('1-2')).toBe('1-2');
    expect(csvCell('50% off + free post')).toBe('50% off + free post');
  });

  it.each([
    ['a leading space', ' =1+1'],
    ['a no-break space', ' =1+1'],
    ['an ideographic space', '　=1+1'],
    ['a zero-width space', '​=1+1'],
    ['a zero-width joiner', '‍+1'],
    ['a word joiner', '⁠@SUM(A1)'],
    ['a byte-order mark', String.fromCharCode(0xfeff) + '=1+1'],
    ['a mix of them', ' ​ -2+3'],
  ])('neutralises a formula after %s', (_name, input) => {
    expect(csvCell(input)).toBe(`'${input}`);
  });

  it.each([
    ['a line feed', '\n=1+1'],
    ['a line feed and text', '\nhello'],
    ['a carriage return', '\rhello'],
    ['a tab', '\thello'],
  ])('neutralises a cell that starts with %s', (_name, input) => {
    const cell = csvCell(input);

    expect(cell.startsWith('"\'')).toBe(true);
    expect(cell.endsWith('"')).toBe(true);
  });

  it.each([
    ['＝', 'full-width ='],
    ['＋', 'full-width +'],
    ['－', 'full-width -'],
    ['＠', 'full-width @'],
  ])('neutralises %s (%s) at the start of a cell', (char) => {
    expect(csvCell(`${char}1`)).toBe(`'${char}1`);
  });

  it('puts the quote after a semicolon or a tab inside a cell and quotes the cell', () => {
    expect(csvCell('a;=1+1;b')).toBe(`"a;'=1+1;b"`);
    expect(csvCell('a; =1+1')).toBe(`"a; '=1+1"`);
    expect(csvCell('a\t@x')).toBe(`"a\t'@x"`);
    expect(csvCell('a;​-2')).toBe(`"a;​'-2"`);
  });

  it('quotes a cell that holds a semicolon or a tab', () => {
    expect(csvCell('one; two')).toBe('"one; two"');
    expect(csvCell('one\ttwo')).toBe('"one\ttwo"');
  });

  it('writes a number only when it is finite', () => {
    expect(csvCell(Number.NaN)).toBe('');
    expect(csvCell(Number.POSITIVE_INFINITY)).toBe('');
    expect(csvCell(-3)).toBe('-3');
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

describe('a hostile report', () => {
  const HOSTILE = [
    '=1+1',
    ' =1+1',
    '+1',
    '-1',
    '@SUM(A1)',
    ' =1',
    '​+1',
    '＝1',
    '＋1',
    '－1',
    '＠x',
    'x;=1+1',
    'x\t=1+1',
    '\n=1+1',
    '\r@x',
    '\t-1',
  ];
  // A cell, at the start of a line or after a comma, semicolon or tab, that a spreadsheet could read as a
  // formula (a quoted cell is looked at from inside its opening quote).
  const RISKY = /(^|[,;\t])"?[\s​-‍⁠]*[=+\-@＝＋－＠]/;

  it.each(HOSTILE)('leaves no risky cell when the merchant, title and slug are %j', (hostile) => {
    const csv = buildClicksCsv({
      merchant: { name: hostile, slug: 'x' },
      from_date: '2026-03-10',
      to_date: '2026-03-11',
      offers: [offer(hostile, [['2026-03-10', 1]], hostile), offer(hostile, [], hostile)],
    });

    // The strictest reading: every physical line, also those inside a quoted cell with a line break.
    const lines = csv.slice(1).split(/\r\n|\n|\r/);
    expect(lines.filter((line) => RISKY.test(line))).toEqual([]);
  });
});

describe('the size of the file', () => {
  it('is measured in UTF-8 bytes, not characters', () => {
    expect(csvByteLength('abc')).toBe(3);
    expect(csvByteLength('é')).toBe(2);
    expect(csvByteLength('日')).toBe(3);
  });

  it('is pinned for a report of 100 offers over 366 days with 40-character titles', () => {
    const days = Array.from({ length: 366 }, (_, i): [string, number] => [
      new Date(Date.UTC(2025, 0, 1 + i)).toISOString().slice(0, 10),
      i % 90,
    ]);
    const title = (i: number) => `Free sample offer ${String(i).padStart(3, '0')} deluxe pk`;
    const offers = Array.from({ length: 100 }, (_, i) =>
      offer(title(i).padEnd(40, 'x'), days, title(i).toLowerCase().replaceAll(' ', '-'))
    );

    const bytes = csvByteLength(
      buildClicksCsv({
        merchant: { name: 'Acme', slug: 'acme' },
        from_date: '2025-01-01',
        to_date: '2026-01-01',
        offers,
      })
    );

    // 36,600 rows, the most one report can hold. Pinned so that a longer row (a new column, a longer date) is a
    // visible change; with titles of this length the largest report still fits under the limit.
    expect(bytes).toBe(3399254);
    expect(bytes).toBeLessThan(MAX_CSV_BYTES);
  });

  it('has a curated message for a file over the limit', () => {
    expect(MAX_CSV_BYTES).toBe(4 * 1024 * 1024);
    expect(CSV_TOO_LARGE_MESSAGE).toBe(
      'This report is too large to download as one file. Choose a shorter date range.'
    );
  });
});

describe('clicksCsvFilename from unvalidated input', () => {
  it('never carries a path or odd characters, whatever the dates are', () => {
    expect(clicksCsvFilename('acme', '../../evil', 'x/y')).toMatch(/^[a-z0-9.-]+\.csv$/);
    expect(clicksCsvFilename('acme', '../../evil', 'x/y')).toBe(
      'freejoy-clicks-acme-date-to-date.csv'
    );
  });
});
