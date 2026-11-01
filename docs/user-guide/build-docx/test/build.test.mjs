import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { GUIDE_DIR, IMAGE } from '../src/config.mjs';
import { fitImage, loadImage, readPngSize, shotBase } from '../src/figures.mjs';
import { LinkRegistry } from '../src/links.mjs';
import { buildPlan } from '../src/plan.mjs';
import { slugify } from '../src/markdown.mjs';

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

test('fitImage keeps the aspect ratio and the size limits', () => {
  const desktop = fitImage('images/a-d.png', { width: 1440, height: 900 });
  assert.equal(Math.round(desktop.widthCm * 10), IMAGE.desktopMaxWidthCm * 10);
  assert.ok(Math.abs(desktop.heightCm / desktop.widthCm - 900 / 1440) < 1e-9);

  const phone = fitImage('images/a-m.png', { width: 390, height: 844 });
  assert.equal(phone.widthCm, IMAGE.mobileMaxWidthCm);

  const tall = fitImage('images/a-m.png', { width: 390, height: 6676 });
  assert.equal(tall.heightCm, IMAGE.maxHeightCm);
  assert.ok(tall.widthCm < IMAGE.mobileMaxWidthCm);

  const capped = fitImage('images/a-d.png', { width: 1440, height: 900 }, 5);
  assert.equal(capped.widthCm, 5);
  assert.equal(shotBase('images/home-m.png'), 'images/home');
});

test('loadImage names the page and line when an image is missing', () => {
  assert.throws(() => loadImage('images/nope-d.png', GUIDE_DIR, '02-browse-and-search.md:21'), /Missing image "images\/nope-d.png".*02-browse-and-search\.md:21/);
});

test('every Markdown link and heading of the real guide resolves', () => {
  const { registry, segments } = buildPlan();
  assert.ok(registry.entries.length > 100);
  assert.ok(segments.length > 15);
  const chapter = registry.resolve('02-browse-and-search.md#step-2-search', '01-home-and-navigation.md', 'test');
  assert.equal(chapter.kind, 'internal');
  assert.equal(registry.resolve('README.md', '03-offer-details.md', 'test').bookmark, '_Contents');
  assert.equal(registry.resolve('../developer/README.md', 'README.md', 'test').kind, 'plain');
  assert.equal(registry.resolve('https://example.com', 'README.md', 'test').kind, 'external');
});

test('broken links fail with a message that says where', () => {
  const registry = new LinkRegistry(['a.md']);
  registry.addHeading({ page: 'a.md', level: 1, text: 'Title' });
  assert.throws(() => registry.resolve('a.md#missing', 'a.md', 'a.md:3'), /no heading "#missing" in a\.md/);
  assert.throws(() => registry.resolve('b.md', 'a.md', 'a.md:4'), /b\.md is not a page of the guide/);
  assert.throws(() => registry.resolve('../developer/does-not-exist.md', 'a.md', 'a.md:5'), /does not exist/);
  assert.equal(registry.resolve('#title', 'a.md', 'a.md:6').kind, 'internal');
});
