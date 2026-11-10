/**
 * Privacy: the public catalogue pages set no cookies, read no session cookie and store nothing in
 * the browser. The one exception is the visitor's own Saved list (FREEJ-28), which lives in
 * `lib/saved/saved-store.ts` and nowhere else. The catalogue API call itself is checked in
 * unit/api/catalogue-headers.test.ts.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it, vi } from 'vitest';
import { render } from '@testing-library/react';

const mocks = vi.hoisted(() => ({ cookies: vi.fn(), headers: vi.fn() }));
vi.mock('next/headers', () => ({ cookies: mocks.cookies, headers: mocks.headers }));
vi.mock('next/navigation', () => ({
  notFound: () => {
    throw new Error('NEXT_NOT_FOUND');
  },
  usePathname: () => '/',
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn(), back: vi.fn() }),
}));

import CategoryPage from '@/app/(public)/category/[slug]/page';
import OfferPage from '@/app/(public)/freebies/[slug]/page';
import FreebiesPage from '@/app/(public)/freebies/page';
import HomePage from '@/app/(public)/page';
import { CatalogueHeading, CatalogueResults } from '@/components/catalogue/catalogue-results';
import { HomeSections } from '@/components/home/home-sections';
import { DEFAULT_QUERY } from '@/lib/catalogue';

const SRC_DIR = path.resolve(__dirname, '../../..');
const PUBLIC_DIRS = [
  'app/(public)',
  'components/catalogue',
  'components/home',
  'components/offer',
  'components/site',
  'lib/catalogue',
  'lib/seo',
];
const STORAGE_OR_COOKIE =
  /document\.cookie|\bcookies\s*\(|set-cookie|localStorage|sessionStorage|from\s+['"]next\/headers['"]/i;

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const full = path.join(dir, entry);
    if (statSync(full).isDirectory()) return sourceFiles(full);
    return /\.(ts|tsx)$/.test(entry) ? [full] : [];
  });
}

describe('public catalogue pages', () => {
  it('render without ever touching the cookie jar or request headers', async () => {
    render(await HomePage());
    render(await HomeSections({ demo: true }));
    render(await FreebiesPage({ searchParams: Promise.resolve({ q: 'tea' }) }));
    render(await CatalogueHeading({ query: DEFAULT_QUERY }));
    render(await CatalogueResults({ query: DEFAULT_QUERY }));
    render(
      await CategoryPage({
        params: Promise.resolve({ slug: 'pets' }),
        searchParams: Promise.resolve({}),
      })
    );
    render(
      await OfferPage({ params: Promise.resolve({ slug: 'glow-boosting-face-cream-sample' }) })
    );

    expect(mocks.cookies).not.toHaveBeenCalled();
    expect(mocks.headers).not.toHaveBeenCalled();
  });

  it('contain no code that sets a cookie or uses browser storage', () => {
    const offenders = PUBLIC_DIRS.flatMap((dir) => sourceFiles(path.join(SRC_DIR, dir)))
      .filter((file) => STORAGE_OR_COOKIE.test(readFileSync(file, 'utf8')))
      .map((file) => path.relative(SRC_DIR, file));

    expect(offenders).toEqual([]);
  });

  it('keeps the Saved list store as the only code that touches browser storage', () => {
    const offenders = sourceFiles(SRC_DIR)
      .filter((file) => !file.includes(`${path.sep}__tests__${path.sep}`))
      .filter((file) => /localStorage|sessionStorage|indexedDB/.test(readFileSync(file, 'utf8')))
      .map((file) => path.relative(SRC_DIR, file).split(path.sep).join('/'));

    expect(offenders).toEqual(['lib/saved/saved-store.ts']);
  });
});
