// @vitest-environment node
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const APP_DIR = path.resolve(__dirname, '../../../app');
const PUBLIC_DIR = path.join(APP_DIR, '(public)');

/** The public segments that may hold a loading.tsx: each cannot 404 or redirect. A new one is added here on purpose. */
const PUBLIC_SEGMENTS_WITH_LOADING = [
  'about',
  'affiliate-disclosure',
  'alerts',
  'contact',
  'cookies',
  'developers',
  'partners',
  'privacy',
  'saved',
  'terms',
];

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const full = path.join(dir, entry);
    return statSync(full).isDirectory() ? walk(full) : [full];
  });
}

function findLoadingFiles(dir: string): string[] {
  return walk(dir).filter((file) => /^loading\.(tsx|ts|jsx|js)$/.test(path.basename(file)));
}

/**
 * Files at or below `dir` that decide the response status: a page, layout, template or default that calls
 * notFound(), redirect() or permanentRedirect(). A call hidden in a helper that the file imports is not
 * detected; keep such decisions in the page itself.
 */
function findStatusDecidingFiles(dir: string): string[] {
  return walk(dir).filter(
    (file) =>
      /^(page|layout|template|default)\.(tsx|ts|jsx|js)$/.test(path.basename(file)) &&
      /\b(notFound|redirect|permanentRedirect)\(/.test(readFileSync(file, 'utf8'))
  );
}

/**
 * A loading.tsx wraps its page in a Suspense boundary, and the dynamic public pages call notFound() after
 * awaiting the catalogue. With a boundary above them React streams the shell first, so the response is
 * already 200 when notFound() runs: every unknown offer or category becomes a soft 404. Measured on the
 * production build: with no loading.tsx above them /category/nope answers 404, with one it answers 200.
 * In-page <Suspense> boundaries (home sections, catalogue results) are fine; they sit below the
 * notFound() decision.
 *
 * A loading.tsx covers its own segment and every segment below it, so the rule is: no loading file at a
 * level that has a notFound() or redirect() page, layout or template in its subtree (a redirect() after the
 * shell is sent becomes a client-side redirect, not a 307). The public pages without that risk (about,
 * privacy, alerts, ...) each have their own, and only those: the allowlist above is exact. The rest rely
 * on the global navigation indicator (FREEJ-67).
 */
describe('Loading boundaries and real 404s', () => {
  describe('Given the app directory', () => {
    it('should have no root loading.tsx', () => {
      expect(existsSync(path.join(APP_DIR, 'loading.tsx'))).toBe(false);
    });

    it('should have no loading file above a public file that calls notFound() or redirect()', () => {
      const offenders = findLoadingFiles(PUBLIC_DIR).filter(
        (file) => findStatusDecidingFiles(path.dirname(file)).length > 0
      );
      expect(offenders).toEqual([]);
    });

    it('should still have notFound() pages under the public routes (so the rule above is not vacuous)', () => {
      expect(findStatusDecidingFiles(PUBLIC_DIR).length).toBeGreaterThan(0);
    });

    it('should hold public loading files only in the allowed segments', () => {
      const found = findLoadingFiles(PUBLIC_DIR).map((file) =>
        path.relative(PUBLIC_DIR, path.dirname(file)).split(path.sep).join('/')
      );
      expect(found.sort()).toEqual([...PUBLIC_SEGMENTS_WITH_LOADING].sort());
    });

    it('should give each allowed public segment a loading file with the spinner', () => {
      for (const segment of PUBLIC_SEGMENTS_WITH_LOADING) {
        const file = path.join(PUBLIC_DIR, segment, 'loading.tsx');
        expect(existsSync(file), segment).toBe(true);
        expect(readFileSync(file, 'utf8'), segment).toContain('route-skeleton');
      }
    });

    it('should keep a loading file for each segment that relied on the root one', () => {
      for (const segment of ['dashboard', '(admin)/admin', '(auth)', 'pricing']) {
        expect(existsSync(path.join(APP_DIR, segment, 'loading.tsx')), segment).toBe(true);
      }
    });
  });
});
