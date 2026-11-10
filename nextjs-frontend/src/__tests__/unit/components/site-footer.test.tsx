import { describe, expect, it } from 'vitest';
import { render, screen, within } from '@testing-library/react';

import { SiteFooter } from '@/components/site/site-footer';
import { FOOTER_LINKS } from '@/components/site/nav-items';

describe('site footer links', () => {
  it('links the retailer page from the footer navigation', () => {
    render(<SiteFooter />);

    const nav = screen.getByRole('navigation', { name: 'Legal and about' });
    expect(within(nav).getByRole('link', { name: 'For retailers' })).toHaveAttribute(
      'href',
      '/partners'
    );
  });

  it('keeps the existing links and lists each destination once', () => {
    const hrefs = FOOTER_LINKS.map((link) => link.href);

    expect(hrefs).toEqual(
      expect.arrayContaining([
        '/about',
        '/contact',
        '/privacy',
        '/cookies',
        '/terms',
        '/affiliate-disclosure',
        '/partners',
      ])
    );
    expect(new Set(hrefs).size).toBe(hrefs.length);
  });
});
