// Fonts, colours and Word styles. Brand-neutral: slate blues and greys only.
import { AlignmentType, TabStopType } from 'docx';
import { TEXT_WIDTH_TW } from './config.mjs';

export const COLORS = {
  text: '222B33',
  heading: '1F3B4D',
  accent: '3D6B80',
  muted: '5F6B75',
  border: 'B8C4CC',
  tableHeader: 'E3EBF0',
  calloutFill: 'EEF3F6',
  codeFill: 'F2F4F5',
  link: '1F5F8B',
};

const BODY_FONT = 'Calibri';
const CODE_FONT = 'Consolas';
const BODY_SIZE = 22; // half-points: 11 pt

const tocStyle = (level, indentTw) => ({
  id: `TOC${level}`,
  name: `toc ${level}`,
  basedOn: 'Normal',
  next: 'Normal',
  quickFormat: true,
  run: { size: level === 1 ? 22 : 21, bold: level === 1 },
  paragraph: {
    spacing: { before: level === 1 ? 120 : 0, after: 40 },
    indent: { left: indentTw },
    tabStops: [{ type: TabStopType.RIGHT, position: TEXT_WIDTH_TW, leader: 'dot' }],
  },
});

export const styles = {
  default: {
    document: {
      run: { font: BODY_FONT, size: BODY_SIZE, color: COLORS.text, language: { value: 'en-GB' } },
      paragraph: { spacing: { after: 120, line: 276 } },
    },
    title: {
      run: { font: BODY_FONT, size: 72, bold: true, color: COLORS.heading },
      paragraph: { spacing: { before: 0, after: 200 } },
    },
    heading1: {
      run: { font: BODY_FONT, size: 40, bold: true, color: COLORS.heading },
      paragraph: {
        spacing: { before: 0, after: 240 },
        pageBreakBefore: true,
        keepNext: true,
        keepLines: true,
        outlineLevel: 0,
      },
    },
    heading2: {
      run: { font: BODY_FONT, size: 30, bold: true, color: COLORS.heading },
      paragraph: { spacing: { before: 360, after: 120 }, keepNext: true, keepLines: true, outlineLevel: 1 },
    },
    heading3: {
      run: { font: BODY_FONT, size: 25, bold: true, color: COLORS.accent },
      paragraph: { spacing: { before: 280, after: 100 }, keepNext: true, keepLines: true, outlineLevel: 2 },
    },
    heading4: {
      run: { font: BODY_FONT, size: 22, bold: true, color: COLORS.accent },
      paragraph: { spacing: { before: 200, after: 80 }, keepNext: true, keepLines: true, outlineLevel: 3 },
    },
    hyperlink: {
      run: { color: COLORS.link, underline: { type: 'single' } },
    },
  },
  paragraphStyles: [
    {
      id: 'Subtitle',
      name: 'Subtitle',
      basedOn: 'Normal',
      next: 'Normal',
      quickFormat: true,
      run: { size: 32, color: COLORS.accent },
      paragraph: { spacing: { after: 240 } },
    },
    {
      id: 'TitleMeta',
      name: 'Title Meta',
      basedOn: 'Normal',
      run: { size: 24, color: COLORS.muted },
      paragraph: { spacing: { after: 80 } },
    },
    {
      id: 'TOCHeading',
      name: 'TOC Heading',
      basedOn: 'Normal',
      next: 'Normal',
      quickFormat: true,
      run: { size: 40, bold: true, color: COLORS.heading },
      paragraph: { pageBreakBefore: true, spacing: { after: 160 }, keepNext: true },
    },
    {
      id: 'Hint',
      name: 'Hint',
      basedOn: 'Normal',
      run: { size: 19, italics: true, color: COLORS.muted },
      paragraph: { spacing: { after: 200 } },
    },
    {
      id: 'Header',
      name: 'header',
      basedOn: 'Normal',
      run: { size: 18, color: COLORS.muted },
      paragraph: { spacing: { after: 0 } },
    },
    {
      id: 'Footer',
      name: 'footer',
      basedOn: 'Normal',
      run: { size: 18, color: COLORS.muted },
      paragraph: { spacing: { after: 0 } },
    },
    tocStyle(1, 0),
    tocStyle(2, 284),
    tocStyle(3, 568),
    {
      id: 'Caption',
      name: 'caption',
      basedOn: 'Normal',
      next: 'Normal',
      quickFormat: true,
      run: { size: 19, italics: true, color: COLORS.muted },
      paragraph: { alignment: AlignmentType.CENTER, spacing: { before: 0, after: 200 }, keepLines: true },
    },
    {
      id: 'TableText',
      name: 'Table Text',
      basedOn: 'Normal',
      run: { size: 20 },
      paragraph: { spacing: { before: 0, after: 0, line: 252 } },
    },
    {
      id: 'ListText',
      name: 'List Text',
      basedOn: 'Normal',
      paragraph: { spacing: { after: 70 } },
    },
    {
      id: 'Callout',
      name: 'Callout',
      basedOn: 'Normal',
      paragraph: {
        spacing: { before: 60, after: 60 },
        indent: { left: 200, right: 100 },
        shading: { type: 'clear', fill: COLORS.calloutFill, color: 'auto' },
        border: { left: { style: 'single', size: 24, color: COLORS.accent, space: 8 } },
        keepLines: true,
      },
    },
    {
      id: 'CodeBlock',
      name: 'Code Block',
      basedOn: 'Normal',
      run: { font: CODE_FONT, size: 19 },
      paragraph: {
        spacing: { before: 80, after: 160, line: 252 },
        indent: { left: 200, right: 100 },
        shading: { type: 'clear', fill: COLORS.codeFill, color: 'auto' },
        border: { left: { style: 'single', size: 12, color: COLORS.border, space: 8 } },
        keepLines: true,
      },
    },
  ],
  characterStyles: [
    {
      id: 'CodeChar',
      name: 'Code Char',
      run: { font: CODE_FONT, size: 20, color: '2A3640', shading: { type: 'clear', fill: COLORS.codeFill, color: 'auto' } },
    },
  ],
};
