// Decides what goes into the Word file, in which order, and registers every heading.
import fs from 'node:fs';
import {
  GUIDE_DIR,
  INDEX_FILE,
  PARTS,
  PREFACE_SECTION,
  SKIPPED_INDEX_SECTIONS,
  WORD_NOTE_PREFIX,
} from './config.mjs';
import { LinkRegistry } from './links.mjs';
import { loadPage, plainText, splitIndexPage } from './markdown.mjs';

function headingLevelOf(token) {
  return Number(token.tag.slice(1));
}

/** Throws unless every index section and every .md file in the folder is accounted for. */
function assertCoverage(sections, coveredPages) {
  const expected = new Set([
    PREFACE_SECTION,
    ...SKIPPED_INDEX_SECTIONS,
    ...PARTS.map((part) => part.indexSection),
  ]);
  for (const section of sections) {
    if (!expected.has(section.title)) {
      throw new Error(
        `${INDEX_FILE} has a section "${section.title}" that build-docx/src/config.mjs does not place. ` +
          'Add it to PARTS, PREFACE_SECTION or SKIPPED_INDEX_SECTIONS.',
      );
    }
  }
  for (const title of expected) {
    if (!sections.some((section) => section.title === title)) {
      throw new Error(`${INDEX_FILE} no longer has the section "${title}" that config.mjs expects.`);
    }
  }
  const onDisk = fs.readdirSync(GUIDE_DIR).filter((name) => name.endsWith('.md'));
  for (const name of onDisk) {
    if (!coveredPages.has(name)) {
      throw new Error(`${name} is not listed in build-docx/src/config.mjs, so it would be missing from the Word guide.`);
    }
  }
  for (const name of coveredPages) {
    if (!onDisk.includes(name)) throw new Error(`config.mjs lists ${name}, which does not exist.`);
  }
}

/** Reads "Last updated: 2026-10-30 ... commit 5a8851d" from the index intro. */
function readVersion(preamble) {
  for (let i = 0; i < preamble.length; i += 1) {
    if (preamble[i].type !== 'inline') continue;
    const text = plainText(preamble[i]);
    if (!text.startsWith('Last updated:')) continue;
    const date = /(\d{4}-\d{2}-\d{2})/.exec(text);
    const commit = /commit ([0-9a-f]{7,40})/.exec(text);
    if (!date || !commit) break;
    return { isoDate: date[1], commit: commit[1] };
  }
  throw new Error(`${INDEX_FILE}: could not find the "Last updated: <date> ... commit <hash>" line.`);
}

/** Drops the "Word version: ..." pointer paragraph: it is about this file, so it is not part of it. */
function withoutWordNote(preamble) {
  const kept = [];
  for (let i = 0; i < preamble.length; i += 1) {
    const isNote =
      preamble[i].type === 'paragraph_open' && plainText(preamble[i + 1]).startsWith(WORD_NOTE_PREFIX);
    if (isNote) i += 2;
    else kept.push(preamble[i]);
  }
  return kept;
}

export function buildPlan() {
  const index = loadPage(INDEX_FILE);
  const split = splitIndexPage(index.tokens);
  const { sections } = split;
  const preamble = withoutWordNote(split.preamble);
  const coveredPages = new Set([INDEX_FILE, ...PARTS.flatMap((part) => part.files)]);
  assertCoverage(sections, coveredPages);

  const registry = new LinkRegistry(coveredPages);
  const segments = [];
  const section = (title) => sections.find((s) => s.title === title);

  function addTokens(page, tokens, shift) {
    for (const token of tokens) {
      if (token.type !== 'heading_open') continue;
      if (token.level !== 0) throw new Error(`${page}: headings inside lists or quotes are not supported`);
      const inline = tokens[tokens.indexOf(token) + 1];
      const text = plainText(inline);
      registry.addHeading({ page, level: headingLevelOf(token) + shift, text, token });
    }
    segments.push({ kind: 'tokens', page, dir: GUIDE_DIR, tokens, shift });
  }

  // Preface: the index page's own "##" heading, intro and notes, shifted up one level.
  const preface = section(PREFACE_SECTION);
  addTokens(INDEX_FILE, [...preface.heading, ...preamble, ...preface.body], -1);

  for (const part of PARTS) {
    const indexSection = section(part.indexSection);
    const entry = registry.addHeading({
      page: INDEX_FILE,
      level: 1,
      text: `Part ${part.number}. ${indexSection.title}`,
      slugText: indexSection.title,
    });
    segments.push({ kind: 'heading', entry });
    addTokens(INDEX_FILE, indexSection.body, -1);
    for (const file of part.files) {
      const page = loadPage(file);
      addTokens(file, page.tokens, 0);
    }
  }

  return { segments, registry, version: readVersion(preamble) };
}
