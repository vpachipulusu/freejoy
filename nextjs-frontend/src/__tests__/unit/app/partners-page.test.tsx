import { describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';

vi.mock('@/app/globals.css', () => ({}));

import PartnersPage, { metadata as partnersMetadata } from '@/app/(public)/partners/page';
import { DRAFT_BANNER_TEXT } from '@/components/legal/legal-page';

/** Same shape as the compliance guard in home-and-legal-pages.test.tsx. */
const COMPLIANCE_CLAIMS =
  /fully compliant|(gdpr|pecr|ico|asa|cma)[- ]compliant|compliant with|ico[- ]registered|registered with the ico|registration number|approved by|certified|verified by|guaranteed|completely free|100% free/i;

const SECTION_HEADINGS = [
  'What Freejoy lists',
  'What a listing must state',
  'How listings are checked and published',
  'Paid and sponsored listings',
  'What we do not do',
  'Get in touch',
];

/** The page text without the shared draft banner, which carries the site's own dash. */
function pageText(container: HTMLElement): string {
  return (container.textContent ?? '').replace(DRAFT_BANNER_TEXT, '');
}

describe('partners page (FREEJ-55)', () => {
  it('has exactly one h1, "List your free sample"', () => {
    render(<PartnersPage />);

    const headings = screen.getAllByRole('heading', { level: 1 });
    expect(headings).toHaveLength(1);
    expect(headings[0]).toHaveTextContent('List your free sample');
  });

  it('has the section headings as h2, in order, with no h3 to skip a level', () => {
    render(<PartnersPage />);

    const h2 = screen.getAllByRole('heading', { level: 2 }).map((heading) => heading.textContent);
    expect(h2).toEqual(SECTION_HEADINGS);
    expect(screen.queryAllByRole('heading', { level: 3 })).toHaveLength(0);
  });

  it('lists what a listing must state as a real list', () => {
    const { container } = render(<PartnersPage />);

    const heading = screen.getByRole('heading', { name: 'What a listing must state' });
    const list = heading.nextElementSibling?.matches('ul')
      ? heading.nextElementSibling
      : heading.nextElementSibling?.nextElementSibling;
    expect(list?.tagName).toBe('UL');
    const items = within(list as HTMLElement)
      .getAllByRole('listitem')
      .map((item) => item.textContent ?? '');
    expect(items).toHaveLength(5);
    expect(items[0]).toMatch(/postage/i);
    expect(items[1]).toMatch(/card/i);
    expect(items[2]).toMatch(/subscription/i);
    expect(items[3]).toMatch(/eligible/i);
    expect(items[4]).toMatch(/how long.*how many|how many.*how long/i);
    expect(container.querySelectorAll('ul')).toHaveLength(1);
  });

  it('says every listing is checked and written by editors, re-checked, and never published by a retailer', () => {
    const { container } = render(<PartnersPage />);
    const text = pageText(container);

    expect(text).toMatch(/editors check and write every listing before it is published/i);
    expect(text).toMatch(/check it again after it goes live/i);
    expect(text).toMatch(/retailer cannot publish a listing directly/i);
    expect(text).toMatch(/ends when the sample runs out/i);
  });

  it('states the Sponsored policy without describing where a label appears or any interface', () => {
    const { container } = render(<PartnersPage />);
    const text = pageText(container);

    expect(text).toMatch(/paid for or otherwise rewarded[^.]*labelled .Sponsored./i);
    expect(text).toMatch(/payment does not change the checks/i);
    // FREEJ-53 builds the label; this page must not describe it.
    expect(text).not.toMatch(
      /badge|pill|banner|ribbon|corner|top of|next to the|above the|below the/i
    );
    expect(text).not.toMatch(/appears|is shown|are shown|displayed/i);
  });

  it('says Freejoy does not collect visitors’ names or addresses for a retailer', () => {
    const { container } = render(<PartnersPage />);

    expect(pageText(container)).toMatch(
      /does not collect visitors.{1,3} names or addresses on a retailer.{1,3}s behalf/i
    );
  });

  it('links to /contact and /affiliate-disclosure with meaningful text', () => {
    render(<PartnersPage />);

    expect(screen.getByRole('link', { name: 'contact page' })).toHaveAttribute('href', '/contact');
    expect(screen.getByRole('link', { name: 'affiliate disclosure' })).toHaveAttribute(
      'href',
      '/affiliate-disclosure'
    );
  });

  it('has no external links, no address or email, and no phone number', () => {
    const { container } = render(<PartnersPage />);

    expect(container.querySelectorAll('a[href^="http"]')).toHaveLength(0);
    expect(container.querySelectorAll('a[href^="mailto:"], a[href^="tel:"]')).toHaveLength(0);
    expect(container.textContent).not.toMatch(/@[a-z0-9-]+\.[a-z]{2,}/i);
  });

  it('collects nothing: no form, input, textarea, select or button', () => {
    const { container } = render(<PartnersPage />);

    expect(container.querySelector('form')).toBeNull();
    expect(container.querySelector('input')).toBeNull();
    expect(container.querySelector('textarea')).toBeNull();
    expect(container.querySelector('select')).toBeNull();
    expect(container.querySelector('button')).toBeNull();
  });

  it('uses no em dashes or en dashes in its own copy', () => {
    const { container } = render(<PartnersPage />);

    expect(pageText(container)).not.toMatch(/[–—]/);
    expect(String(partnersMetadata.title)).not.toMatch(/[–—]/);
    expect(String(partnersMetadata.description)).not.toMatch(/[–—]/);
  });

  it('makes no compliance, registration or approval claims and no figures', () => {
    const { container } = render(<PartnersPage />);

    expect(container.textContent).not.toMatch(COMPLIANCE_CLAIMS);
    expect(pageText(container)).not.toMatch(/\d/);
  });

  it('keeps the draft banner and "Not yet reviewed", like the other editorial pages', () => {
    render(<PartnersPage />);

    expect(screen.getByText(DRAFT_BANNER_TEXT)).toBeInTheDocument();
    expect(screen.getByText(/last reviewed: not yet reviewed/i)).toBeInTheDocument();
  });
});

describe('partners page metadata', () => {
  it('sets a title, a description, a canonical path and Open Graph from the SEO helpers', () => {
    expect(partnersMetadata.title).toBe('List your free sample');
    expect(partnersMetadata.description).toMatch(/retailers and brands/i);
    expect(partnersMetadata.alternates?.canonical).toBe('/partners');
    expect(partnersMetadata.openGraph).toMatchObject({
      type: 'website',
      locale: 'en_GB',
      url: '/partners',
      title: partnersMetadata.title,
    });
  });

  it('is kept out of search engines while it is a draft, like the other editorial pages', () => {
    expect(partnersMetadata.robots).toEqual({ index: false });
  });
});
