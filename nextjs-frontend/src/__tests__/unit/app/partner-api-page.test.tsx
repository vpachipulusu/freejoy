import { describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';

vi.mock('@/app/globals.css', () => ({}));

import PartnerApiPage, { metadata } from '@/app/(public)/developers/partner-api/page';
import PartnersPage from '@/app/(public)/partners/page';
import sitemap from '@/app/sitemap';
import { operationAnchor } from '@/components/developers/operation-anchor';
import { DRAFT_BANNER_TEXT } from '@/components/legal/legal-page';
import { KEY_LOOKING } from '@/lib/admin-catalogue/partner-key-schema';
import { OPERATION_CONTENT, PARTNER_BASE_EXAMPLE } from '@/lib/partner-api/content';
import { listOperations, PARTNER_OPENAPI } from '@/lib/partner-api/schema';

const COMPLIANCE_CLAIMS =
  /fully compliant|(gdpr|pecr|ico|asa|cma)[- ]compliant|compliant with|ico[- ]registered|approved by|certified|verified by|guaranteed|completely free|100% free/i;
const SECTION_HEADINGS = [
  'Before you start',
  'Making a request',
  'Authentication',
  'Endpoints',
  'Answers and errors',
  'Limits',
  'What a click means',
  'Download the API description',
];

function pageText(container: HTMLElement): string {
  /** The page text without the shared draft shell (its banner and its "Not yet reviewed" line). */
  return (container.textContent ?? '')
    .replace(DRAFT_BANNER_TEXT, '')
    .replace('Last reviewed: Not yet reviewed', '');
}

/** The text nodes of the page, one space between them, so a path ends where its element ends. */
function spacedText(container: HTMLElement): string {
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
  const parts: string[] = [];
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    parts.push(node.textContent ?? '');
  }
  return parts.join(' ');
}

const names = () => listOperations().map((operation) => `${operation.method} ${operation.path}`);

describe('partner API page (FREEJ-59)', () => {
  it('has exactly one h1 and the section headings as h2, in order', () => {
    render(<PartnerApiPage />);

    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Freejoy partner API');
    expect(screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent)).toEqual(
      SECTION_HEADINGS
    );
  });

  it('never skips a heading level: h3 for an endpoint, h4 inside it', () => {
    const { container } = render(<PartnerApiPage />);

    const levels = [...container.querySelectorAll('h1, h2, h3, h4, h5, h6')].map((heading) =>
      Number(heading.tagName.slice(1))
    );
    expect(levels[0]).toBe(1);
    levels.slice(1).forEach((level, index) => {
      expect(level - levels[index]).toBeLessThanOrEqual(1);
    });
    expect(levels).toContain(3);
    expect(levels).toContain(4);
  });

  it('has one h3 for every operation of the schema, named by method and path', () => {
    render(<PartnerApiPage />);

    const h3 = screen.getAllByRole('heading', { level: 3 }).map((heading) => heading.textContent);
    expect(h3).toEqual(names());
  });

  it('lists every operation in a contents list whose links reach its heading', () => {
    const { container } = render(<PartnerApiPage />);

    const endpoints = screen.getByRole('heading', { level: 2, name: 'Endpoints' });
    const list = endpoints.nextElementSibling?.nextElementSibling as HTMLElement;
    const links = within(list).getAllByRole('link');
    expect(links.map((link) => link.textContent)).toEqual(names());
    for (const operation of listOperations()) {
      const target = container.querySelector(`#${operationAnchor(operation.operationId)}`);
      expect(target?.tagName).toBe('H3');
      expect(links.map((l) => l.getAttribute('href'))).toContain(
        `#${operationAnchor(operation.operationId)}`
      );
    }
  });

  it('names no partner path that the schema does not have', () => {
    const { container } = render(<PartnerApiPage />);

    const named = spacedText(container).match(/\/partner\/v1[A-Za-z0-9_{}/.-]*/g) ?? [];
    const known = new Set(['/partner/v1', ...Object.keys(PARTNER_OPENAPI.paths)]);
    expect(named.length).toBeGreaterThan(0);
    for (const path of named) {
      expect(known.has(path.replace(/[.,]$/, ''))).toBe(true);
    }
  });

  it('shows, for every operation, an example request and answer in a named <pre><code> region', () => {
    const { container } = render(<PartnerApiPage />);

    for (const operation of listOperations()) {
      const name = `${operation.method} ${operation.path}`;
      for (const kind of ['request', 'answer']) {
        const region = screen.getByRole('region', { name: `Example ${kind} for ${name}` });
        expect(region.tagName).toBe('PRE');
        expect(region.querySelector(':scope > code')).not.toBeNull();
        expect(region).toHaveAttribute('tabindex', '0');
        expect(region.className).toContain('overflow-x-auto');
      }
    }
    expect(container.querySelectorAll('pre')).toHaveLength(
      listOperations().length * 2 + listOperations().filter((operation) => operation.body).length
    );
  });

  it('renders each request example as real lines, with no literal backslash and n', () => {
    render(<PartnerApiPage />);

    for (const operation of listOperations()) {
      const block = screen.getByRole('region', {
        name: `Example request for ${operation.method} ${operation.path}`,
      });
      const lines = (block.textContent ?? '').split('\n');

      expect(lines.length).toBeGreaterThan(1);
      expect(block.textContent).not.toContain('\\n');
      expect(lines[0]).toMatch(/^curl .* \\$/);
      expect(block.textContent).toBe(OPERATION_CONTENT[operation.operationId].request);
    }
  });

  it('puts every table in a named, focusable scroll region with column and row header cells', () => {
    const { container } = render(<PartnerApiPage />);

    const tables = [...container.querySelectorAll('table')];
    expect(tables.length).toBeGreaterThanOrEqual(5);
    for (const table of tables) {
      const caption = table.querySelector('caption')?.textContent ?? '';
      const region = screen.getByRole('region', { name: caption });
      expect(region).toBe(table.parentElement);
      expect(region).toHaveAttribute('tabindex', '0');
      expect(region.className).toContain('overflow-x-auto');
      expect(screen.getAllByRole('table', { name: caption })).toContain(table);
      expect(table.querySelectorAll('thead th[scope="col"]').length).toBeGreaterThanOrEqual(2);
      for (const row of table.querySelectorAll('tbody tr')) {
        expect(row.querySelector('th[scope="row"]')).not.toBeNull();
      }
    }
  });

  it('describes the parameters of the clicks endpoint from the schema', () => {
    render(<PartnerApiPage />);

    const table = screen.getByRole('table', { name: 'Parameters of GET /partner/v1/clicks' });
    const rows = within(table).getAllByRole('row').slice(1);
    expect(rows.map((row) => within(row).getAllByRole('rowheader')[0].textContent)).toEqual([
      'from',
      'to',
    ]);
    expect(
      within(rows[0])
        .getAllByRole('cell')
        .map((cell) => cell.textContent)
    ).toEqual(['query', 'Yes', 'string (date)', 'First UTC day, YYYY-MM-DD.']);
  });

  it('says an endpoint with no parameters takes none', () => {
    render(<PartnerApiPage />);

    const me = screen.getByRole('heading', { name: 'GET /partner/v1/me' }).closest('section');
    expect(me).toHaveTextContent('This endpoint takes no parameters and no request body.');
    expect(me?.textContent).not.toContain('Parameters of');
  });

  it('gives each endpoint the statuses that are true of it', () => {
    render(<PartnerApiPage />);

    const me = screen.getByRole('table', { name: 'Answers of GET /partner/v1/me' });
    const clicks = screen.getByRole('table', { name: 'Answers of GET /partner/v1/clicks' });
    expect(me).not.toHaveTextContent(/range|invalid_range/);
    expect(clicks).toHaveTextContent('invalid_range');
    expect(clicks).toHaveTextContent('The date range message');
  });

  it('defines the address in the examples as the site origin followed by /partner/v1', () => {
    const { container } = render(<PartnerApiPage />);

    const text = pageText(container);
    expect(text).toMatch(
      /\$FREEJOY_PARTNER_URL is the address of the Freejoy website followed by \/partner\/v1/
    );
    expect(text).toContain(PARTNER_BASE_EXAMPLE);
    expect(text).toContain('GET /partner/v1/me is called as $FREEJOY_PARTNER_URL/me');
    for (const operation of listOperations()) {
      const relative = operation.path.slice('/partner/v1'.length);
      // A templated path is called with a concrete id: the part before the first `{` is the same.
      expect(OPERATION_CONTENT[operation.operationId].request).toContain(
        `$FREEJOY_PARTNER_URL${relative.split('{')[0]}`
      );
    }
  });

  it('says the API is not open to every retailer, promises no date and asks for no "yet"', () => {
    const { container } = render(<PartnerApiPage />);

    const text = pageText(container);
    expect(text).toMatch(/not open to every retailer\./);
    expect(text).toMatch(/cannot say when a request will be answered/);
    expect(text).not.toMatch(
      /\b(yet|soon|coming|will be (open|available)|by (january|spring|q[1-4]))\b/i
    );
  });

  it('talks about the contact page in the future tense, as /partners does, and promises nothing urgent', () => {
    const { container } = render(<PartnerApiPage />);

    const text = pageText(container);
    expect(text).toMatch(
      /contact page will show how to reach Freejoy once a contact route is published\. When it does, use it to ask for a key/
    );
    expect(screen.getByRole('link', { name: 'contact page' })).toHaveAttribute('href', '/contact');
    expect(text).not.toMatch(/tell us|we will revoke|we will/i);
    expect(text).toMatch(/ask the person at Freejoy who gave you the key/);
  });

  it('says nothing is published without an editor, and that what is sent is a draft', () => {
    const { container } = render(<PartnerApiPage />);

    const text = pageText(container);
    expect(text).toMatch(/Nothing is published through this API/);
    expect(text).toMatch(/editors check and write every listing before it goes live/);
    expect(text).toMatch(/What you send is stored as a draft/);
    expect(text).not.toMatch(/Today it can only read|it cannot change anything/);
    expect(screen.getByRole('link', { name: 'page for retailers' })).toHaveAttribute(
      'href',
      '/partners'
    );
  });

  it('says every error has one of the listed messages except a refused date range', () => {
    const { container } = render(<PartnerApiPage />);

    const text = pageText(container);
    expect(text).toMatch(
      /except a refused date range, which has its own message and the code invalid_range/
    );
    expect(text).not.toMatch(/An error always has one of these messages/);
  });

  it('says what a click is and is not, with the words of the API itself', () => {
    const { container } = render(<PartnerApiPage />);

    const heading = screen.getByRole('heading', { name: 'What a click means' });
    expect(heading.nextElementSibling).toHaveTextContent(/not a sale, a sign-up or a claim/);
    const text = pageText(container);
    expect(text).toMatch(/clicks twice counts twice/);
    expect(text).toMatch(
      /Counts can also be overstated by automated traffic that does not identify itself as automated\./
    );
    expect(text).toMatch(/Recording is rate limited for each offer/);
    expect(text).toMatch(
      /in a very busy minute, or when many clicks on one offer come from one network in a minute, some clicks are not recorded and the count can be low/
    );
    expect(text).toMatch(/deleted after 395 days/);
    // The recording rate figure must not be on the page (600 as a field length is not that figure).
    expect(text).not.toMatch(/600 (clicks|times|requests)|a minute for each offer|still sent on/);
  });

  it('expands its abbreviations and relates UTC to UK time', () => {
    const { container } = render(<PartnerApiPage />);

    const text = pageText(container);
    expect(text).toMatch(/\(application programming interface\)/);
    expect(text).toMatch(/UTC, which is Coordinated Universal Time: the same as UK time in winter/);
    expect(text).toMatch(/one hour behind it in summer/);
    expect(text).toMatch(/256 KiB \(262,144 bytes\)/);
    expect(text).toMatch(/JSON, a common text format/);
    expect(text).toMatch(/OpenAPI document \(a standard way to describe an API\)/);
  });

  it('shows field and parameter names as code, and no backtick reaches the page', () => {
    const { container } = render(<PartnerApiPage />);

    const codes = [...container.querySelectorAll('code')].map((code) => code.textContent);
    expect(codes).toEqual(
      expect.arrayContaining([
        'starts_before_retention',
        'earliest_complete_date',
        'offers_truncated',
        'invalid_range',
      ])
    );
    expect(container.textContent).not.toContain('`');
  });

  it('offers the committed document for download from the static files, naming the format', () => {
    render(<PartnerApiPage />);

    const link = screen.getByRole('link', { name: 'Download the OpenAPI document (JSON)' });
    expect(link).toHaveAttribute('href', '/partner-openapi.json');
    expect(link).toHaveAttribute('download', 'freejoy-partner-openapi.json');
    expect(screen.getByText(/version 1\.0\.0\./)).toBeInTheDocument();
  });

  it('has no key, no real-looking address and no live API page', () => {
    const { container } = render(<PartnerApiPage />);

    const text = pageText(container);
    expect(text).not.toMatch(KEY_LOOKING);
    expect(text).toContain('fj_production_<key id>_<secret>');
    expect(text).not.toMatch(/swagger|redoc/i);
    for (const url of text.match(/https?:\/\/[^"\s,]+/g) ?? []) {
      expect(new URL(url).hostname.endsWith('.example')).toBe(true);
    }
  });

  it('makes no compliance claims, uses no em or en dash and keeps the draft shell', () => {
    const { container } = render(<PartnerApiPage />);

    expect(pageText(container)).not.toMatch(COMPLIANCE_CLAIMS);
    expect(pageText(container)).not.toMatch(/[–—]/);
    expect(screen.getByText(DRAFT_BANNER_TEXT)).toBeInTheDocument();
    expect(screen.getByText(/last reviewed: not yet reviewed/i)).toBeInTheDocument();
  });

  it('gives a long path and a long name room to wrap inside the page, not widen it', () => {
    const { container } = render(<PartnerApiPage />);

    for (const heading of container.querySelectorAll('h3')) {
      expect(heading.className).toContain('[overflow-wrap:anywhere]');
    }
    for (const code of container.querySelectorAll('p code, li code')) {
      expect(code.className).toContain('[overflow-wrap:anywhere]');
    }
  });

  it('stops the build when an operation of the schema has no prose', async () => {
    vi.resetModules();
    vi.doMock('@/lib/partner-api/schema', async (importOriginal) => {
      const original = await importOriginal<typeof import('@/lib/partner-api/schema')>();
      return {
        ...original,
        listOperations: () => [
          ...original.listOperations(),
          { ...original.listOperations()[0], operationId: 'partner-brand-new' },
        ],
      };
    });
    const { default: Page } = await import('@/app/(public)/developers/partner-api/page');

    expect(() => render(<Page />)).toThrow(/"partner-brand-new" with no entry/);
    vi.doUnmock('@/lib/partner-api/schema');
  });

  it('stops the build when an operation answers with a status nothing describes', async () => {
    vi.resetModules();
    vi.doMock('@/lib/partner-api/schema', async (importOriginal) => {
      const original = await importOriginal<typeof import('@/lib/partner-api/schema')>();
      return {
        ...original,
        listOperations: () =>
          original.listOperations().map((operation) => ({
            ...operation,
            responses: [...operation.responses, { status: '418' }],
          })),
      };
    });
    const { default: Page } = await import('@/app/(public)/developers/partner-api/page');

    expect(() => render(<Page />)).toThrow(/Status 418 of partner-me has no row/);
    vi.doUnmock('@/lib/partner-api/schema');
  });
});

describe('partner API page metadata and search engines', () => {
  it('sets a title, description, canonical path and Open Graph from the SEO helpers', () => {
    expect(metadata.title).toBe('Partner API');
    expect(String(metadata.description)).toMatch(/retailer/i);
    expect(String(metadata.description)).not.toMatch(/[–—]/);
    expect(metadata.alternates?.canonical).toBe('/developers/partner-api');
    expect(metadata.openGraph).toMatchObject({
      type: 'website',
      locale: 'en_GB',
      url: '/developers/partner-api',
    });
  });

  it('is kept out of search engines while it is a draft, like /partners', () => {
    expect(metadata.robots).toEqual({ index: false });
  });

  it('is not in the generated sitemap', async () => {
    const entries = await sitemap();

    expect(entries.map((entry) => entry.url).join(',')).not.toContain('developers');
    expect(entries.map((entry) => entry.url).join(',')).not.toContain('partner-openapi');
  });
});

describe('the page for retailers links to the API documentation', () => {
  it('links to it, says what is counted, and keeps the closing sentence last', () => {
    const { container } = render(<PartnersPage />);

    expect(screen.getByRole('link', { name: 'partner API documentation' })).toHaveAttribute(
      'href',
      '/developers/partner-api'
    );
    const text = container.textContent ?? '';
    expect(text).toMatch(/click through from a listing to your site/);
    expect(text).toMatch(/This is not open to every retailer\./);
    expect(text.replace('Not yet reviewed', '')).not.toMatch(/\byet\b/);
    const paragraphs = [...container.querySelectorAll('p')].map((p) => p.textContent ?? '');
    expect(paragraphs[paragraphs.length - 1]).toMatch(/There is no listing form on this page/);
  });
});
