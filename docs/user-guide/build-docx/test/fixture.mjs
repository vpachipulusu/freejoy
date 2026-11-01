// Builds a tiny throw-away guide in the OS temp folder so tests can exercise the generator
// without touching docs/user-guide.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import JSZip from 'jszip';
import { GUIDE_DIR } from '../src/config.mjs';

export const README = `# Fixture guide

**Last updated:** 2026-01-02. The screenshots were taken from the app at commit \`abc1234\` on 2 January 2026.

**Word version:** [Fixture.docx](Fixture.docx).

## Part A section

| Page | What it explains |
| --- | --- |
| [1. One](01-one.md) | The first chapter |

## Please read this first

### A note

Some text with a [link to chapter one](01-one.md#lists).

## Developer documentation

Skipped.
`;

export const CHAPTER = `# 1. One

[Back to the guide index](README.md)

## Lists

1. First item with \`code\`, **bold** and *italic*
   - nested bullet
     1. deeper ordered
2. Second item

- Two links: [site](https://example.com/a) and [again](https://example.com/b)

## Table and quote

| A | B |
| --- | ---: |
| left | 2 |

> **Tip.** A callout paragraph.

## Screens

Desktop:

![A short page on desktop](images/404-d.png)

Phone:

![A short page on a phone](images/404-m.png)

![A very tall page on a phone](images/home-m.png)

\`\`\`text
fenced code
\`\`\`
`;

/** Creates the fixture folder. Pass `chapter` to replace the chapter text. */
export function makeFixture({ chapter = CHAPTER, extraFiles = {}, readme = README } = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'freejoy-docx-test-'));
  fs.mkdirSync(path.join(dir, 'images'));
  for (const name of ['404-d.png', '404-m.png', 'home-d.png', 'home-m.png']) {
    fs.copyFileSync(path.join(GUIDE_DIR, 'images', name), path.join(dir, 'images', name));
  }
  fs.writeFileSync(path.join(dir, 'README.md'), readme);
  fs.writeFileSync(path.join(dir, '01-one.md'), chapter);
  for (const [name, text] of Object.entries(extraFiles)) fs.writeFileSync(path.join(dir, name), text);
  const config = {
    guideDir: dir,
    outputFile: path.join(dir, 'out.docx'),
    indexFile: 'README.md',
    prefaceSection: 'Please read this first',
    skippedSections: ['Developer documentation'],
    parts: [{ number: 1, indexSection: 'Part A section', files: ['01-one.md'] }],
    legalChapterFile: '01-one.md',
  };
  return { dir, config, cleanup: () => fs.rmSync(dir, { recursive: true, force: true }) };
}

/** Reads one part of a built .docx as text. */
export async function readPart(file, part = 'word/document.xml') {
  const zip = await JSZip.loadAsync(fs.readFileSync(file));
  return zip.file(part).async('string');
}
