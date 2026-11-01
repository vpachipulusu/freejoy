// Block-level Markdown (headings, paragraphs, lists, tables, quotes, code, figures) -> docx elements.
import path from 'node:path';
import {
  AlignmentType,
  Bookmark,
  BorderStyle,
  HeadingLevel,
  ImageRun,
  LevelFormat,
  LineRuleType,
  Paragraph,
  ShadingType,
  SimpleField,
  Table,
  TableBorders,
  TableCell,
  TableLayoutType,
  TableRow,
  TextRun,
  VerticalAlign,
  WidthType,
} from 'docx';
import { IMAGE, TEXT_WIDTH_TW, TWIPS_PER_CM } from './config.mjs';
import { fitImage, isDesktopShot, isMobileShot, loadImage, shotBase } from './figures.mjs';
import { renderInline } from './inline.mjs';
import { closeIndex, plainText } from './markdown.mjs';
import { COLORS } from './styles.mjs';

const HEADINGS = [
  null,
  HeadingLevel.HEADING_1,
  HeadingLevel.HEADING_2,
  HeadingLevel.HEADING_3,
  HeadingLevel.HEADING_4,
];
const LIST_INDENT_TW = 567;
const LIST_HANGING_TW = 340;
const BULLETS = ['•', '–', '◦'];
const SHORT_LABEL_MAX = 40;

/** Mutable counters shared by the whole build. */
export function createState() {
  return { figure: 0, numbering: [], images: [], tables: 0 };
}

const at = (ctx) => `${ctx.page}:${ctx.cursor.line}`;

// ---------------------------------------------------------------- figures

/** Returns [{src, alt}] when the paragraph at `i` holds only images, else null. */
function figuresOf(tokens, i) {
  if (tokens[i]?.type !== 'paragraph_open') return null;
  const parts = tokens[i + 1].children ?? [];
  const figures = [];
  for (const child of parts) {
    if (child.type === 'image') figures.push({ src: child.attrGet('src'), alt: child.content.trim() });
    else if (child.type === 'softbreak') continue;
    else if (child.type === 'text' && child.content.trim() === '') continue;
    else return null;
  }
  return figures.length > 0 ? figures : null;
}

const nextIsFigure = (tokens, i) => figuresOf(tokens, i) !== null;

function pictureAndCaption(figure, ctx, widthCapCm) {
  if (!figure.alt) throw new Error(`${at(ctx)}: image "${figure.src}" has no alt text`);
  const image = loadImage(figure.src, ctx.dir, at(ctx));
  const fit = fitImage(figure.src, image.size, widthCapCm);
  ctx.state.figure += 1;
  const number = ctx.state.figure;
  ctx.state.images.push({ src: figure.src, alt: figure.alt, page: ctx.page, ...fit });
  const picture = new Paragraph({
    alignment: AlignmentType.CENTER,
    keepNext: true,
    spacing: { before: 120, after: 60 },
    children: [
      new ImageRun({
        type: 'png',
        data: image.data,
        transformation: { width: fit.widthPx, height: fit.heightPx },
        altText: { name: path.basename(figure.src), title: figure.alt, description: figure.alt },
      }),
    ],
  });
  const caption = new Paragraph({
    style: 'Caption',
    children: [
      new TextRun('Figure '),
      new SimpleField('SEQ Figure \\* ARABIC', String(number)),
      new TextRun(`. ${figure.alt}`),
    ],
  });
  return [picture, caption];
}

const NO_BORDER = { style: BorderStyle.NONE, size: 0, color: 'auto' };
const NO_BORDERS = { top: NO_BORDER, bottom: NO_BORDER, left: NO_BORDER, right: NO_BORDER };

function spacerAfterTable() {
  return new Paragraph({
    spacing: { before: 0, after: 0, line: 120, lineRule: LineRuleType.EXACT },
    children: [],
  });
}

function pairCell(figure, cellCm, label, ctx) {
  const cap = cellCm - 2 * IMAGE.cellPaddingCm;
  const children = [];
  if (label) {
    children.push(
      new Paragraph({
        keepNext: true,
        alignment: AlignmentType.CENTER,
        spacing: { after: 0 },
        children: renderInline(label.children, { ...ctx, bold: true }),
      }),
    );
  }
  children.push(...pictureAndCaption(figure, ctx, cap));
  return new TableCell({
    width: { size: Math.round(cellCm * TWIPS_PER_CM), type: WidthType.DXA },
    verticalAlign: VerticalAlign.TOP,
    borders: NO_BORDERS,
    margins: { left: 80, right: 80, top: 0, bottom: 0 },
    children,
  });
}

function pairTable(desktop, mobile, label, ctx) {
  const left = IMAGE.pairDesktopCellCm;
  const right = IMAGE.pairMobileCellCm;
  const table = new Table({
    alignment: AlignmentType.CENTER,
    layout: TableLayoutType.FIXED,
    width: { size: TEXT_WIDTH_TW, type: WidthType.DXA },
    columnWidths: [Math.round(left * TWIPS_PER_CM), Math.round(right * TWIPS_PER_CM)],
    borders: TableBorders.NONE,
    rows: [
      new TableRow({
        cantSplit: true,
        children: [pairCell(desktop, left, null, ctx), pairCell(mobile, right, label, ctx)],
      }),
    ],
  });
  return [table, spacerAfterTable()];
}

/** A short "Phone:" style line between a desktop and a phone shot of the same screen. */
function shortLabelAt(tokens, i) {
  if (tokens[i]?.type !== 'paragraph_open' || figuresOf(tokens, i)) return null;
  const inline = tokens[i + 1];
  const text = plainText(inline);
  return text.length <= SHORT_LABEL_MAX && text.endsWith(':') ? inline : null;
}

/**
 * Emits the figure paragraph at `i` (a desktop shot immediately followed, optionally after a short
 * label, by the phone shot of the same screen becomes one borderless two-column table).
 * Returns the index of the next unprocessed token.
 */
function renderFigureRun(tokens, i, figures, ctx, out) {
  if (figures.length === 1 && isDesktopShot(figures[0].src)) {
    let j = i + 3;
    const label = shortLabelAt(tokens, j);
    if (label) j += 3;
    const next = figuresOf(tokens, j);
    if (
      next?.length === 1 &&
      isMobileShot(next[0].src) &&
      shotBase(next[0].src) === shotBase(figures[0].src)
    ) {
      out.push(...pairTable(figures[0], next[0], label, ctx));
      return j + 3;
    }
  }
  for (const figure of figures) out.push(...pictureAndCaption(figure, ctx, Infinity));
  return i + 3;
}

// ---------------------------------------------------------------- text blocks

function heading(tokens, i, ctx) {
  const entry = ctx.registry.byToken.get(tokens[i]);
  if (!entry) throw new Error(`${at(ctx)}: heading was not registered`);
  const level = Math.min(Math.max(entry.level, 1), 4);
  const children = renderInline(tokens[i + 1].children, { ...ctx, plainLinks: true });
  return new Paragraph({
    heading: HEADINGS[level],
    children: [new Bookmark({ id: entry.bookmark, children })],
  });
}

function paragraph(tokens, i, ctx) {
  const options = {
    children: renderInline(tokens[i + 1].children, ctx),
    keepNext: nextIsFigure(tokens, i + 3) || undefined,
  };
  if (ctx.quote) options.style = 'Callout';
  else if (ctx.item) options.style = 'ListText';
  if (ctx.item && !ctx.quote) {
    if (ctx.item.used) {
      options.indent = { left: ctx.item.left };
    } else {
      options.numbering = { reference: ctx.item.reference, level: 0 };
      ctx.item.used = true;
    }
  }
  return new Paragraph(options);
}

function codeBlock(token, ctx) {
  const lines = token.content.replace(/\n$/, '').split('\n');
  return new Paragraph({
    style: 'CodeBlock',
    indent: ctx.item ? { left: ctx.item.left } : undefined,
    children: lines.map((line, k) => new TextRun({ text: line, break: k === 0 ? undefined : 1 })),
  });
}

// ---------------------------------------------------------------- lists

function addNumbering(state, { ordered, start, depth }) {
  const reference = `list-${state.numbering.length + 1}`;
  const left = LIST_INDENT_TW * (depth + 1);
  state.numbering.push({
    reference,
    levels: [
      {
        level: 0,
        format: ordered ? (depth === 0 ? LevelFormat.DECIMAL : LevelFormat.LOWER_LETTER) : LevelFormat.BULLET,
        text: ordered ? '%1.' : BULLETS[depth % BULLETS.length],
        alignment: AlignmentType.LEFT,
        start: ordered ? start : undefined,
        style: { paragraph: { indent: { left, hanging: LIST_HANGING_TW } } },
      },
    ],
  });
  return { reference, left };
}

function list(tokens, i, ctx) {
  const open = tokens[i];
  const end = closeIndex(tokens, i);
  const ordered = open.type === 'ordered_list_open';
  const depth = ctx.listDepth ?? 0;
  const start = Number(open.attrGet('start') ?? 1);
  const { reference, left } = addNumbering(ctx.state, { ordered, start, depth });
  const out = [];
  let j = i + 1;
  while (j < end) {
    const itemEnd = closeIndex(tokens, j);
    const item = { reference, left, used: false };
    out.push(...renderBlocks(tokens.slice(j + 1, itemEnd), { ...ctx, item, listDepth: depth + 1 }));
    j = itemEnd + 1;
  }
  return out;
}

// ---------------------------------------------------------------- tables

const CELL_BORDER = { style: BorderStyle.SINGLE, size: 4, color: COLORS.border };
const TABLE_BORDERS = {
  top: CELL_BORDER,
  bottom: CELL_BORDER,
  left: CELL_BORDER,
  right: CELL_BORDER,
  insideHorizontal: CELL_BORDER,
  insideVertical: CELL_BORDER,
};
const MIN_COLUMN_WEIGHT = 10;
const MAX_COLUMN_WEIGHT = 60;

function readTable(tokens, i) {
  const end = closeIndex(tokens, i);
  const rows = [];
  let row = null;
  let inHead = false;
  for (let j = i + 1; j < end; j += 1) {
    const token = tokens[j];
    if (token.type === 'thead_open') inHead = true;
    else if (token.type === 'thead_close') inHead = false;
    else if (token.type === 'tr_open') row = { header: inHead, cells: [] };
    else if (token.type === 'tr_close') rows.push(row);
    else if (token.type === 'th_open' || token.type === 'td_open') {
      const style = token.attrGet('style') ?? '';
      const align = style.includes('right') ? 'right' : style.includes('center') ? 'center' : 'left';
      row.cells.push({ inline: tokens[j + 1], align });
    }
  }
  return rows;
}

function columnWidths(rows) {
  const count = rows[0].cells.length;
  const weights = Array.from({ length: count }, (_, c) => {
    const longest = Math.max(...rows.map((r) => plainText(r.cells[c].inline).length));
    return Math.min(Math.max(longest, MIN_COLUMN_WEIGHT), MAX_COLUMN_WEIGHT);
  });
  const total = weights.reduce((a, b) => a + b, 0);
  const widths = weights.map((w) => Math.floor((w / total) * TEXT_WIDTH_TW));
  widths[count - 1] += TEXT_WIDTH_TW - widths.reduce((a, b) => a + b, 0);
  return widths;
}

const ALIGN = {
  left: AlignmentType.LEFT,
  right: AlignmentType.RIGHT,
  center: AlignmentType.CENTER,
};

function table(tokens, i, ctx) {
  const rows = readTable(tokens, i);
  const widths = columnWidths(rows);
  ctx.state.tables += 1;
  const built = new Table({
    layout: TableLayoutType.FIXED,
    width: { size: TEXT_WIDTH_TW, type: WidthType.DXA },
    columnWidths: widths,
    borders: TABLE_BORDERS,
    rows: rows.map(
      (row) =>
        new TableRow({
          tableHeader: row.header,
          cantSplit: true,
          children: row.cells.map(
            (cell, c) =>
              new TableCell({
                width: { size: widths[c], type: WidthType.DXA },
                shading: row.header
                  ? { type: ShadingType.CLEAR, fill: COLORS.tableHeader, color: 'auto' }
                  : undefined,
                margins: { top: 60, bottom: 60, left: 100, right: 100 },
                children: [
                  new Paragraph({
                    style: 'TableText',
                    alignment: ALIGN[cell.align],
                    keepNext: row.header || undefined,
                    children: renderInline(cell.inline.children, { ...ctx, bold: row.header }),
                  }),
                ],
              }),
          ),
        }),
    ),
  });
  return [built, spacerAfterTable()];
}

// ---------------------------------------------------------------- dispatcher

export function renderBlocks(tokens, ctx) {
  const out = [];
  let i = 0;
  while (i < tokens.length) {
    const token = tokens[i];
    if (token.map) ctx.cursor.line = token.map[0] + 1;
    switch (token.type) {
      case 'heading_open':
        out.push(heading(tokens, i, ctx));
        i += 3;
        break;
      case 'paragraph_open': {
        const figures = figuresOf(tokens, i);
        if (figures) {
          i = renderFigureRun(tokens, i, figures, ctx, out);
        } else {
          out.push(paragraph(tokens, i, ctx));
          i += 3;
        }
        break;
      }
      case 'bullet_list_open':
      case 'ordered_list_open':
        out.push(...list(tokens, i, ctx));
        i = closeIndex(tokens, i) + 1;
        break;
      case 'table_open':
        out.push(...table(tokens, i, ctx));
        i = closeIndex(tokens, i) + 1;
        break;
      case 'blockquote_open': {
        const end = closeIndex(tokens, i);
        out.push(...renderBlocks(tokens.slice(i + 1, end), { ...ctx, quote: true }));
        i = end + 1;
        break;
      }
      case 'fence':
      case 'code_block':
        out.push(codeBlock(token, ctx));
        i += 1;
        break;
      case 'hr':
        out.push(new Paragraph({ border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: COLORS.border, space: 1 } }, children: [] }));
        i += 1;
        break;
      default:
        throw new Error(`${at(ctx)}: unsupported Markdown block "${token.type}"`);
    }
  }
  return out;
}
