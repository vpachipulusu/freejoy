// Markdown parsing helpers (markdown-it does the real parsing; nothing here uses regex on Markdown).
import fs from 'node:fs';
import path from 'node:path';
import MarkdownIt from 'markdown-it';
import { GUIDE_DIR } from './config.mjs';

// CommonMark + GFM tables and strikethrough (markdown-it defaults). Raw HTML is not allowed in the
// sources, so it stays off: any "<tag>" in the text is shown literally.
export const md = new MarkdownIt({ html: false, linkify: false, typographer: false });

/** Reads and parses one page. */
export function loadPage(name, dir = GUIDE_DIR) {
  const file = path.join(dir, name);
  if (!fs.existsSync(file)) {
    throw new Error(`Source page not found: ${file}`);
  }
  const text = fs.readFileSync(file, 'utf8');
  return { name, dir, text, tokens: md.parse(text, {}) };
}

/** GitHub-style heading anchor, so the Markdown links (#step-2-search) keep working. */
export function slugify(text) {
  return text
    .toLowerCase()
    .trim()
    .replace(/[^\p{L}\p{N}\s_-]/gu, '')
    .replace(/\s/g, '-');
}

/** Plain text of an inline token (text, code and breaks only). */
export function plainText(inline) {
  let out = '';
  for (const child of inline.children ?? []) {
    if (child.type === 'text' || child.type === 'code_inline') out += child.content;
    else if (child.type === 'softbreak' || child.type === 'hardbreak') out += ' ';
  }
  return out.replace(/\s+/g, ' ').trim();
}

/** Index of the token that closes the block opened at `start`. */
export function closeIndex(tokens, start) {
  let depth = 0;
  for (let i = start; i < tokens.length; i += 1) {
    depth += tokens[i].nesting;
    if (depth === 0) return i;
  }
  throw new Error(`Unclosed ${tokens[start].type} block`);
}

/**
 * Splits the index page into its intro (between the "#" title and the first "##") and its
 * "##" sections. The "#" title tokens are dropped: the Word title page replaces them.
 */
export function splitIndexPage(tokens) {
  const preamble = [];
  const sections = [];
  let current = null;
  let i = 0;
  while (i < tokens.length) {
    const token = tokens[i];
    const isTopHeading = token.type === 'heading_open' && token.level === 0;
    if (isTopHeading && token.tag === 'h1') {
      i += 3;
    } else if (isTopHeading && token.tag === 'h2') {
      current = { title: plainText(tokens[i + 1]), heading: tokens.slice(i, i + 3), body: [] };
      sections.push(current);
      i += 3;
    } else {
      (current ? current.body : preamble).push(token);
      i += 1;
    }
  }
  return { preamble, sections };
}
