import { describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';

vi.mock('@/app/globals.css', () => ({}));

import PartnerApiPage, { metadata } from '@/app/(public)/developers/partner-api/page';
import PartnersPage from '@/app/(public)/partners/page';
import sitemap from '@/app/sitemap';
import { DRAFT_BANNER_TEXT } from '@/components/legal/legal-page';
import { KEY_LOOKING } from '@/lib/admin-catalogue/partner-key-schema';
import { OPERATION_CONTENT } from '@/lib/partner-api/content';
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
  return (container.textContent ?? '').replace(DRAFT_BANNER_TEXT, '');
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
    expect(h3).toEqual(
      listOperations().map((operation) => `${operation.method} ${operation.path}`)
    );
    expect(h3).toEqual(expect.arrayContaining(['GET /partner/v1/me', 'GET /partner/v1/clicks']));
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

  it('shows a request and an answer for every operation, each in a <pre><code> region', () => {
    const { container } = render(<PartnerApiPage />);

    const blocks = [...container.querySelectorAll('pre')];
    expect(blocks).toHaveLength(listOperations().length * 2);
    for (const block of blocks) {
      expect(block.querySelector(':scope > code')).not.toBeNull();
      expect(block).toHaveAttribute('role', 'region');
      expect(block.getAttribute('aria-label')).toMatch(/^Example (request|answer) for GET /);
      expect(block).toHaveAttribute('tabindex', '0');
      expect(block.className).toContain('overflow-x-auto');
    }
    for (const content of Object.values(OPERATION_CONTENT)) {
      expect(container.textContent).toContain(content.request.split('\n')[0]);
    }
  });

  it('puts every table in a focusable scroll region, with a caption and column header cells', () => {
    const { container } = render(<PartnerApiPage />);

    const tables = [...container.querySelectorAll('table')];
    expect(tables.length).toBeGreaterThanOrEqual(5);
    for (const table of tables) {
      const region = table.parentElement as HTMLElement;
      expect(region).toHaveAttribute('role', 'region');
      expect(region).toHaveAttribute('tabindex', '0');
      expect(region.className).toContain('overflow-x-auto');
      expect(region.getAttribute('aria-label')).toBe(table.querySelector('caption')?.textContent);
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

  it('says the API is not open to every retailer, how to ask, and promises no date', () => {
    const { container } = render(<PartnerApiPage />);

    const text = pageText(container);
    expect(text).toMatch(/not open to every retailer yet/);
    expect(screen.getByRole('link', { name: 'contact page' })).toHaveAttribute('href', '/contact');
    expect(text).toMatch(/cannot say when a request will be answered/);
    expect(text).not.toMatch(
      /\b(soon|coming|will be (open|available)|by (january|spring|q[1-4]))\b/i
    );
  });

  it('says nothing is published without an editor, and that it only reads today', () => {
    const { container } = render(<PartnerApiPage />);

    const text = pageText(container);
    expect(text).toMatch(/Nothing is published through this API/);
    expect(text).toMatch(/editors check and write every listing before it goes live/);
    expect(screen.getByRole('link', { name: 'page for retailers' })).toHaveAttribute(
      'href',
      '/partners'
    );
  });

  it('says what a click is and is not, with the words of the API itself', () => {
    const { container } = render(<PartnerApiPage />);

    const heading = screen.getByRole('heading', { name: 'What a click means' });
    expect(heading.nextElementSibling).toHaveTextContent(/not a sale, a sign-up or a claim/);
    expect(pageText(container)).toMatch(/clicks twice counts twice/);
    expect(pageText(container)).toMatch(/limited to 600 clicks for one offer in a minute/);
    expect(pageText(container)).toMatch(/deleted after 395 days/);
  });

  it('offers the committed document for download from the static files', () => {
    render(<PartnerApiPage />);

    const link = screen.getByRole('link', { name: /Download the OpenAPI document/ });
    expect(link).toHaveAttribute('href', '/partner-openapi.json');
    expect(link).toHaveAttribute('download', 'freejoy-partner-openapi.json');
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
  it('has the link, and still has no digits and no new heading', () => {
    const { container } = render(<PartnersPage />);

    expect(screen.getByRole('link', { name: 'partner API documentation' })).toHaveAttribute(
      'href',
      '/developers/partner-api'
    );
    expect(container.textContent).toMatch(/not open to every retailer yet/);
  });
});
