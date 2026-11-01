import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { build, sequentialHyperlinkIds } from '../src/build.mjs';
import { GUIDE_DIR, IMAGE, defaultConfig } from '../src/config.mjs';
import { layoutImage, loadImage, readPngSize, shotBase } from '../src/figures.mjs';
import { LinkRegistry } from '../src/links.mjs';
import { slugify } from '../src/markdown.mjs';
import { buildPlan } from '../src/plan.mjs';
import { CHAPTER, README, makeFixture, readPart } from './fixture.mjs';

const count = (text, pattern) => (text.match(pattern) ?? []).length;

test('slugify follows the GitHub anchor rules used by the Markdown links', () => {
  assert.equal(slugify('Step 2. Search'), 'step-2-search');
  assert.equal(slugify('What Freejoy stores in your browser'), 'what-freejoy-stores-in-your-browser');
  assert.equal(slugify('Food & drink (examples)'), 'food--drink-examples');
});

test('readPngSize reads real screenshots and rejects other files', () => {
  const home = fs.readFileSync(path.join(GUIDE_DIR, 'images', 'home-d.png'));
  assert.deepEqual(readPngSize(home), { width: 1440, height: home.readUInt32BE(20) });
  assert.throws(() => readPngSize(Buffer.from('not a png at all, just text')), /not a PNG/);
});

test('layoutImage keeps the aspect ratio, shrinks a little, and slices anything taller', () => {
  const desktop = layoutImage('images/a-d.png', { width: 1440, height: 900 });
  assert.equal(desktop.count, 1);
  assert.equal(desktop.widthCm, IMAGE.desktopMaxWidthCm);
  assert.ok(Math.abs(desktop.heightCm / desktop.widthCm - 900 / 1440) < 1e-9);

  const phone = layoutImage('images/a-m.png', { width: 390, height: 844 });
  assert.equal(phone.count, 1);
  assert.equal(phone.widthCm, IMAGE.mobileMaxWidthCm);

  // A little too tall: shrinks to fit one page instead of slicing.
  const almost = layoutImage('images/a-m.png', { width: 390, height: 1500 });
  assert.equal(almost.count, 1);
  assert.equal(almost.heightCm, IMAGE.maxHeightCm);
  assert.ok(almost.widthCm >= IMAGE.mobileMaxWidthCm * IMAGE.minShrinkMobile);

  // Far too tall: full width, equal slices that tile the picture exactly.
  const tall = layoutImage('images/a-m.png', { width: 390, height: 6676 });
  assert.equal(tall.widthCm, IMAGE.mobileMaxWidthCm);
  assert.equal(tall.count, 5);
  assert.ok(tall.slices.every((slice) => slice.heightCm <= IMAGE.maxHeightCm));
  assert.equal(tall.slices[0].topPct, 0);
  assert.equal(tall.slices.at(-1).bottomPct, 0);
  tall.slices.slice(1).forEach((slice, k) => {
    assert.ok(Math.abs(slice.topPct - (100 - tall.slices[k].bottomPct)) < 1e-9);
  });

  const capped = layoutImage('images/a-d.png', { width: 1440, height: 900 }, 5);
  assert.equal(capped.widthCm, 5);
  assert.equal(shotBase('images/home-m.png'), 'images/home');
});

test('loadImage names the page and line when an image is missing', () => {
  assert.throws(
    () => loadImage('images/nope-d.png', GUIDE_DIR, '02-browse-and-search.md:21'),
    /Missing image "images\/nope-d.png".*02-browse-and-search\.md:21/,
  );
});

test('broken links fail with a message that says where', () => {
  const registry = new LinkRegistry(['a.md']);
  registry.addHeading({ page: 'a.md', level: 1, text: 'Title' });
  assert.throws(() => registry.resolve('a.md#missing', 'a.md', 'a.md:3'), /no heading "#missing" in a\.md/);
  assert.throws(() => registry.resolve('b.md', 'a.md', 'a.md:4'), /b\.md is not a page of the guide/);
  assert.throws(() => registry.resolve('../developer/does-not-exist.md', 'a.md', 'a.md:5'), /does not exist/);
  assert.equal(registry.resolve('#title', 'a.md', 'a.md:6').kind, 'internal');
});

test('the real guide plans, and the link kinds resolve as expected', () => {
  const { registry } = buildPlan();
  assert.ok(registry.entries.length > 100);
  const chapter = registry.resolve('02-browse-and-search.md#step-2-search', '01-home-and-navigation.md', 'test');
  assert.equal(chapter.kind, 'internal');
  assert.equal(registry.resolve('README.md', '03-offer-details.md', 'test').bookmark, '_Contents');
  assert.equal(registry.resolve('../developer/README.md', 'README.md', 'test').kind, 'plain');
  assert.equal(registry.resolve('https://example.com', 'README.md', 'test').kind, 'external');
});

test('the real guide renders end to end: every link and image resolves', async () => {
  const outputFile = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'freejoy-docx-real-')), 'real.docx');
  try {
    const result = await build({ ...defaultConfig, outputFile });
    assert.ok(result.state.figure > 100, `only ${result.state.figure} figures`);
    assert.ok(result.state.pictures >= result.state.figure);
    const xml = await readPart(outputFile);
    const anchors = [...xml.matchAll(/<w:hyperlink [^>]*w:anchor="([^"]+)"/g)].map((m) => m[1]);
    const bookmarks = new Set([...xml.matchAll(/<w:bookmarkStart [^>]*w:name="([^"]+)"/g)].map((m) => m[1]));
    assert.ok(anchors.length > 100);
    assert.deepEqual(anchors.filter((anchor) => !bookmarks.has(anchor)), []);
  } finally {
    fs.rmSync(path.dirname(outputFile), { recursive: true, force: true });
  }
});

test('fixture guide: lists, nested lists, tables, callouts, pairs and slices', async () => {
  const fixture = makeFixture();
  try {
    const { state } = await build(fixture.config);
    const xml = await readPart(fixture.config.outputFile);

    // ordered list, its nested bullet, the deeper ordered list, and the separate bullet list
    assert.equal(new Set([...xml.matchAll(/<w:numId w:val="(\d+)"/g)].map((m) => m[1])).size, 4);
    assert.equal(state.numbering.length, 4);
    assert.ok(xml.includes('w:val="Callout"'));
    assert.ok(xml.includes('<w:tblHeader/>'));
    assert.ok(xml.includes('w:fill="E3EBF0"'));
    assert.ok(xml.includes('w:val="CodeBlock"'));
    assert.ok(xml.includes('w:val="CodeChar"'));

    // the 404 desktop + phone shots sit in one two-cell table; the tall phone shot is cut in 3
    const tables = xml.match(/<w:tbl>.*?<\/w:tbl>/gs);
    const pair = tables.filter((table) => table.includes('<w:drawing>'));
    assert.equal(pair.length, 1);
    assert.equal(count(pair[0], /<w:drawing>/g), 2);
    assert.equal(count(pair[0], /<w:tc>/g), 2);
    assert.equal(count(xml, /\(part \d of 3\)/g) / 2, 3); // each slice has descr + title
    assert.equal(count(xml, /<a:srcRect t="/g), 3);
    assert.equal(state.figure, 3);
    assert.equal(state.pictures, 5);
    assert.equal(count(xml, /SEQ Figure/g), 3);
    assert.equal(count(xml, /w:val="Caption"/g), 3);
  } finally {
    fixture.cleanup();
  }
});

test('build is deterministic, even with external links (random relationship ids)', async () => {
  const fixture = makeFixture();
  try {
    await build(fixture.config);
    const first = fs.readFileSync(fixture.config.outputFile);
    await build(fixture.config);
    const second = fs.readFileSync(fixture.config.outputFile);
    assert.ok(first.equals(second), 'two builds from the same sources differ');
    const xml = await readPart(fixture.config.outputFile);
    const rels = await readPart(fixture.config.outputFile, 'word/_rels/document.xml.rels');
    assert.ok(xml.includes('r:id="rIdLink1"') && xml.includes('r:id="rIdLink2"'));
    assert.ok(rels.includes('Id="rIdLink1"') && rels.includes('Id="rIdLink2"'));
  } finally {
    fixture.cleanup();
  }
});

test('sequentialHyperlinkIds renames only hyperlink relationships, in order of appearance', () => {
  const type = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink';
  const rels =
    `<Relationships><Relationship Id="rIdX9" Type="${type}" Target="https://b" TargetMode="External"/>` +
    `<Relationship Id="rIdA1" Type="${type}" Target="https://a" TargetMode="External"/>` +
    '<Relationship Id="rId5" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/x.png"/></Relationships>';
  const document = '<w:p><w:hyperlink r:id="rIdA1"/><w:hyperlink w:history="1" r:id="rIdX9"/><a:blip r:embed="rId5"/></w:p>';
  const [newDocument, newRels] = sequentialHyperlinkIds(document, rels);
  assert.ok(newDocument.includes('r:id="rIdLink1"/><w:hyperlink w:history="1" r:id="rIdLink2"'));
  assert.ok(newRels.includes('Id="rIdLink1" Type') && newRels.includes('Target="https://a"'));
  assert.ok(newRels.includes('Id="rId5"'));
});

test('a page missing from config.mjs, or an unknown index section, fails the build', async () => {
  const extra = makeFixture({ extraFiles: { '02-forgotten.md': '# 2. Forgotten\n' } });
  try {
    await assert.rejects(build(extra.config), /02-forgotten\.md is not listed/);
  } finally {
    extra.cleanup();
  }
  const section = makeFixture({ readme: `${README}\n## Surprise\n\nx\n` });
  try {
    await assert.rejects(build(section.config), /section "Surprise" that build-docx\/src\/config\.mjs does not place/);
  } finally {
    section.cleanup();
  }
});

test('a list inside a blockquote is rejected instead of silently losing its numbers', async () => {
  const fixture = makeFixture({ chapter: `${CHAPTER}\n> Steps:\n>\n> 1. one\n> 2. two\n` });
  try {
    await assert.rejects(build(fixture.config), /01-one\.md:\d+: a list inside a blockquote is not supported/);
  } finally {
    fixture.cleanup();
  }
});

test('a missing image or link target in a page fails the build with the position', async () => {
  const image = makeFixture({ chapter: `${CHAPTER}\n![Nope](images/nope-d.png)\n` });
  try {
    await assert.rejects(build(image.config), /Missing image "images\/nope-d\.png" referenced at 01-one\.md:/);
  } finally {
    image.cleanup();
  }
  const link = makeFixture({ chapter: `${CHAPTER}\nSee [there](01-one.md#nowhere).\n` });
  try {
    await assert.rejects(build(link.config), /Broken link "01-one\.md#nowhere" at 01-one\.md:/);
  } finally {
    link.cleanup();
  }
});
