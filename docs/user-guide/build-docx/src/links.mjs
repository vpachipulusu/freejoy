// Bookmarks for every heading, and resolution of Markdown links against them.
import fs from 'node:fs';
import path from 'node:path';
import { GUIDE_DIR, INDEX_FILE } from './config.mjs';
import { slugify } from './markdown.mjs';

export const CONTENTS_BOOKMARK = '_Contents';
const EXTERNAL = /^(https?:|mailto:)/i;

/** Word bookmark names are limited to 40 characters, so they are numbered, not slugged. */
function bookmarkName(counter) {
  return `_Toc${String(100000000 + counter)}`;
}

export class LinkRegistry {
  constructor(pageNames) {
    this.pageNames = new Set(pageNames);
    this.entries = []; // every heading, in document order
    this.byAnchor = new Map(); // "<page>#<slug>" -> entry
    this.byToken = new WeakMap(); // heading_open token -> entry
    this.slugCounts = new Map(); // per page, for GitHub-style de-duplication
  }

  /** Registers a heading and returns its entry { level, text, bookmark, page, slug }. */
  addHeading({ page, level, text, token, slugText = text }) {
    const base = slugify(slugText);
    const countKey = `${page}#${base}`;
    const seen = this.slugCounts.get(countKey) ?? 0;
    this.slugCounts.set(countKey, seen + 1);
    const slug = seen === 0 ? base : `${base}-${seen}`;
    const entry = { level, text, page, slug, bookmark: bookmarkName(this.entries.length + 1) };
    this.entries.push(entry);
    this.byAnchor.set(`${page}#${slug}`, entry);
    if (token) this.byToken.set(token, entry);
    return entry;
  }

  /** The first heading of a page, which is the target of a link to the whole page. */
  chapterEntry(page) {
    return this.entries.find((entry) => entry.page === page);
  }

  /**
   * Turns a Markdown link target into one of:
   *   { kind: 'internal', bookmark }  a heading or the Contents page in this document
   *   { kind: 'external', url }       http(s) or mailto
   *   { kind: 'plain' }               a repository file outside the guide (no target in a Word file)
   * Throws, naming the source position, when the target does not exist.
   */
  resolve(href, fromPage, where) {
    if (EXTERNAL.test(href)) return { kind: 'external', url: href };
    const hashAt = href.indexOf('#');
    const rawPath = hashAt === -1 ? href : href.slice(0, hashAt);
    const fragment = hashAt === -1 ? '' : decodeURIComponent(href.slice(hashAt + 1));
    const targetPath = rawPath === '' ? path.join(GUIDE_DIR, fromPage) : path.resolve(GUIDE_DIR, rawPath);
    const inGuide = path.dirname(targetPath) === GUIDE_DIR;
    const page = path.basename(targetPath);

    if (!inGuide) {
      if (!fs.existsSync(targetPath)) {
        throw new Error(`Broken link "${href}" at ${where}: ${targetPath} does not exist`);
      }
      return { kind: 'plain' };
    }
    if (!this.pageNames.has(page)) {
      throw new Error(`Broken link "${href}" at ${where}: ${page} is not a page of the guide`);
    }
    if (page === INDEX_FILE && fragment === '') {
      return { kind: 'internal', bookmark: CONTENTS_BOOKMARK };
    }
    if (fragment === '') {
      const chapter = this.chapterEntry(page);
      if (!chapter) throw new Error(`Broken link "${href}" at ${where}: ${page} has no heading`);
      return { kind: 'internal', bookmark: chapter.bookmark };
    }
    const entry = this.byAnchor.get(`${page}#${fragment}`);
    if (!entry) {
      throw new Error(`Broken link "${href}" at ${where}: no heading "#${fragment}" in ${page}`);
    }
    return { kind: 'internal', bookmark: entry.bookmark };
  }
}
