// Builds docs/user-guide/Freejoy-User-Guide.docx from the Markdown pages.
// Usage: npm run build   (from docs/user-guide/build-docx)
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
import { Bookmark, Document, HeadingLevel, Packer, Paragraph, TextRun } from 'docx';
import JSZip from 'jszip';
import { createState, renderBlocks } from './blocks.mjs';
import { GUIDE_TITLE, MAX_OUTPUT_BYTES, PAGE, defaultConfig } from './config.mjs';
import { contentsPage, emptyFooter, emptyHeader, pageFooter, pageHeader, titlePage } from './front.mjs';
import { buildPlan } from './plan.mjs';
import { styles } from './styles.mjs';

const HYPERLINK_TYPE = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink';

/**
 * docx gives every external hyperlink a random relationship id. Rename them to rIdLink1, rIdLink2...
 * in order of appearance so the same sources always give the same bytes.
 * Returns the rewritten [document.xml, document.xml.rels].
 */
export function sequentialHyperlinkIds(documentXml, relsXml) {
  const hyperlinkRels = new Set();
  for (const rel of relsXml.match(/<Relationship [^>]*\/>/g) ?? []) {
    if (rel.includes(`Type="${HYPERLINK_TYPE}"`)) hyperlinkRels.add(/Id="([^"]+)"/.exec(rel)[1]);
  }
  const renamed = new Map();
  const newDocument = documentXml.replace(/(<w:hyperlink [^>]*?r:id=")([^"]+)(")/g, (all, before, id, after) => {
    if (!hyperlinkRels.has(id)) return all;
    if (!renamed.has(id)) renamed.set(id, `rIdLink${renamed.size + 1}`);
    return `${before}${renamed.get(id)}${after}`;
  });
  const newRels = relsXml.replace(/<Relationship [^>]*\/>/g, (rel) => {
    const id = /Id="([^"]+)"/.exec(rel)[1];
    return renamed.has(id) ? rel.replace(`Id="${id}"`, `Id="${renamed.get(id)}"`) : rel;
  });
  return [newDocument, newRels];
}

/**
 * docx numbers bookmarks and pictures from counters that live for the whole process, so a second
 * build in one process would differ. Renumber both 1, 2, 3... in document order.
 */
export function sequentialDrawingAndBookmarkIds(documentXml) {
  const bookmarkIds = new Map();
  const withBookmarks = documentXml.replace(/(<w:bookmarkStart [^>]*?w:id=")(\d+)(")/g, (all, before, id, after) => {
    bookmarkIds.set(id, String(bookmarkIds.size + 1));
    return `${before}${bookmarkIds.get(id)}${after}`;
  });
  const withEnds = withBookmarks.replace(/(<w:bookmarkEnd [^>]*?w:id=")(\d+)(")/g, (all, before, id, after) =>
    bookmarkIds.has(id) ? `${before}${bookmarkIds.get(id)}${after}` : all,
  );
  let picture = 0;
  return withEnds.replace(/(<wp:docPr id=")(\d+)(")/g, (all, before, id, after) => {
    picture += 1;
    return `${before}${picture}${after}`;
  });
}

/** The generated file must not change from run to run unless the sources do. */
export async function normaliseZip(buffer, isoDate) {
  const fixed = new Date(`${isoDate}T00:00:00Z`);
  const zip = await JSZip.loadAsync(buffer);
  const out = new JSZip();
  const names = Object.keys(zip.files).sort((a, b) => {
    // [Content_Types].xml must stay first for strict readers.
    if (a === '[Content_Types].xml') return -1;
    if (b === '[Content_Types].xml') return 1;
    return a < b ? -1 : a > b ? 1 : 0;
  });
  const text = async (name) => (await zip.file(name).async('nodebuffer')).toString('utf8');
  const [linkedXml, relsXml] = sequentialHyperlinkIds(
    await text('word/document.xml'),
    await text('word/_rels/document.xml.rels'),
  );
  const documentXml = sequentialDrawingAndBookmarkIds(linkedXml);
  const replaced = {
    'word/document.xml': documentXml,
    'word/_rels/document.xml.rels': relsXml,
  };
  for (const name of names) {
    const entry = zip.files[name];
    if (entry.dir) continue;
    let data;
    if (name in replaced) {
      data = Buffer.from(replaced[name], 'utf8');
    } else {
      data = await entry.async('nodebuffer');
      if (name === 'docProps/core.xml') {
        const stamp = `${isoDate}T00:00:00Z`;
        data = Buffer.from(
          data
            .toString('utf8')
            .replace(/(<dcterms:created[^>]*>)[^<]*/, `$1${stamp}`)
            .replace(/(<dcterms:modified[^>]*>)[^<]*/, `$1${stamp}`),
          'utf8',
        );
      }
    }
    out.file(name, data, { date: fixed, createFolders: false });
  }
  return out.generateAsync({ type: 'nodebuffer', compression: 'DEFLATE', compressionOptions: { level: 9 } });
}

function renderSegments(segments, registry, state) {
  const children = [];
  for (const segment of segments) {
    if (segment.kind === 'heading') {
      const { entry } = segment;
      children.push(
        new Paragraph({
          heading: HeadingLevel.HEADING_1,
          children: [new Bookmark({ id: entry.bookmark, children: [new TextRun(entry.text)] })],
        }),
      );
      continue;
    }
    const ctx = {
      page: segment.page,
      dir: segment.dir,
      cursor: { line: 1 },
      links: registry,
      registry,
      state,
    };
    children.push(...renderBlocks(segment.tokens, ctx));
  }
  return children;
}

export async function build(config = defaultConfig) {
  const { segments, registry, version } = buildPlan(config);
  const state = createState();
  const body = renderSegments(segments, registry, state);

  const document = new Document({
    creator: 'Freejoy',
    title: GUIDE_TITLE,
    description: 'Step-by-step guide to the Freejoy public site and admin area',
    features: { updateFields: true },
    styles,
    numbering: { config: state.numbering },
    sections: [
      {
        properties: {
          titlePage: true,
          page: {
            size: { width: PAGE.widthTw, height: PAGE.heightTw },
            margin: {
              top: PAGE.marginTw,
              bottom: PAGE.marginTw,
              left: PAGE.marginTw,
              right: PAGE.marginTw,
              header: 567,
              footer: 567,
            },
          },
        },
        headers: { default: pageHeader(), first: emptyHeader() },
        footers: { default: pageFooter(), first: emptyFooter() },
        children: [
          ...titlePage({ version, registry, legalChapterFile: config.legalChapterFile }),
          ...contentsPage(registry),
          ...body,
        ],
      },
    ],
  });

  const raw = await Packer.toBuffer(document);
  const buffer = await normaliseZip(raw, version.isoDate);
  if (buffer.length > MAX_OUTPUT_BYTES) {
    throw new Error(
      `Output is ${(buffer.length / 1048576).toFixed(1)} MB, over the ${MAX_OUTPUT_BYTES / 1048576} MB limit. ` +
        'Downscale or recompress the PNGs inside this build (never in images/).',
    );
  }
  fs.writeFileSync(config.outputFile, buffer);
  return { file: config.outputFile, bytes: buffer.length, state, registry };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  build()
    .then(({ file, bytes, state, registry }) => {
      console.log(
        `Wrote ${file}\n  ${(bytes / 1048576).toFixed(2)} MB, ${registry.entries.length} headings, ` +
          `${state.figure} figures (${state.pictures} pictures, ${state.sliced} cut into slices), ` +
          `${state.tables} tables, ${state.numbering.length} lists`,
      );
    })
    .catch((error) => {
      console.error(`\nBuild failed: ${error.message}`);
      process.exitCode = 1;
    });
}
