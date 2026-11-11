/**
 * FREEJ-60: `/freebies/<slug>?ad=1` (the bounce from a stale link) forces the commission disclosure on. It can only
 * ADD a disclosure: any other value, a repeated parameter or no parameter leaves the mapped flag as it is.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('next/navigation', async (importOriginal) => ({
  ...(await importOriginal<typeof import('next/navigation')>()),
  notFound: () => {
    throw new Error('NEXT_NOT_FOUND');
  },
  useRouter: () => ({ refresh: vi.fn() }),
}));

const lookup = vi.hoisted(() => ({ offer: null as unknown }));
vi.mock('@/lib/catalogue', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/catalogue')>();
  return {
    ...actual,
    lookupOffer: async () => ({ kind: 'found', offer: lookup.offer }),
  };
});

import OfferPage, { generateMetadata } from '@/app/(public)/freebies/[slug]/page';
import { CTA_LABELS } from '@/components/offer/offer-format';
import { OFFERS } from '@/lib/catalogue/fixtures';
import type { Offer } from '@/lib/catalogue/types';

const base: Offer = {
  ...OFFERS[0],
  demo: false,
  source: 'api',
  sponsored: false,
  earnsCommission: false,
};
const COMMISSION = /is paid a commission by the brand/i;

async function renderOffer(
  earnsCommission: boolean | null,
  searchParams?: Record<string, unknown>
) {
  lookup.offer = { ...base, earnsCommission };
  return render(
    await OfferPage({
      params: Promise.resolve({ slug: base.slug }),
      searchParams: searchParams
        ? Promise.resolve(searchParams as { ad?: string | string[] })
        : undefined,
    })
  );
}

function link(): HTMLElement {
  return screen.getByRole('link', { name: CTA_LABELS[base.offerType] });
}

beforeEach(() => {
  lookup.offer = null;
});

describe('the offer page with ?ad=1', () => {
  it('shows Ad before the link, the commission sentence and a plain /go/ link when the mapped flag is false', async () => {
    const { container } = await renderOffer(false, { ad: '1' });

    expect(
      screen.getByText('Ad').compareDocumentPosition(link()) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy();
    expect(container).toHaveTextContent(COMMISSION);
    expect(link()).toHaveAttribute('href', `/go/${base.id}`);
    expect(link().getAttribute('href')).not.toContain('nd');
  });

  it.each([
    ['ad=0', { ad: '0' }],
    ['ad=true', { ad: 'true' }],
    ['ad=', { ad: '' }],
    ['a repeated ad', { ad: ['1', '1'] }],
    ['ad=1&ad=0', { ad: ['1', '0'] }],
    ['an unrelated parameter', { utm: '1' }],
    ['no parameter', undefined],
  ])(
    'leaves a mapped false flag unchanged for %s: no Ad, link marked ?nd=1',
    async (_name, params) => {
      const { container } = await renderOffer(false, params);

      expect(screen.queryByText('Ad')).toBeNull();
      expect(container).not.toHaveTextContent(/commission/i);
      expect(link()).toHaveAttribute('href', `/go/${base.id}?nd=1`);
    }
  );

  it('never removes a disclosure: a true or unknown flag still discloses without the parameter, and with a wrong one', async () => {
    for (const flag of [true, null]) {
      for (const params of [undefined, { ad: '0' }, { ad: ['1', '1'] }]) {
        const { container, unmount } = await renderOffer(flag, params);
        expect(screen.getByText('Ad')).toBeInTheDocument();
        expect(container).toHaveTextContent(COMMISSION);
        expect(link()).toHaveAttribute('href', `/go/${base.id}`);
        unmount();
      }
    }
  });

  it('leaves the metadata, canonical and Open Graph URL, and the JSON-LD free of the parameter', async () => {
    const { container } = await renderOffer(false, { ad: '1' });

    const metadata = await generateMetadata({ params: Promise.resolve({ slug: base.slug }) });
    expect(metadata.alternates?.canonical).toBe(`/freebies/${base.slug}`);
    expect(JSON.stringify(metadata.openGraph)).not.toContain('ad=');
    const scripts = Array.from(container.querySelectorAll('script[type="application/ld+json"]'));
    expect(scripts.length).toBeGreaterThan(0);
    for (const script of scripts) {
      expect(script.textContent).not.toMatch(/[?&]ad=/);
    }
    expect(container.innerHTML).not.toMatch(/\?ad=1/);
  });
});
