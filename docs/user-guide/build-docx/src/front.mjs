// Title page, Contents page (real TOC field + static entries), header and footer.
import {
  AlignmentType,
  BorderStyle,
  Bookmark,
  Footer,
  Header,
  InternalHyperlink,
  PageNumber,
  Paragraph,
  Run,
  SimpleField,
  Tab,
  TabStopType,
  TextRun,
  XmlAttributeComponent,
  XmlComponent,
} from 'docx';
import { GUIDE_TITLE, LEGAL_CHAPTER_FILE, TEXT_WIDTH_TW } from './config.mjs';
import { CONTENTS_BOOKMARK } from './links.mjs';
import { COLORS } from './styles.mjs';

// ---- Raw field characters: docx has no API for a TOC field that already holds a result.
class FieldCharAttributes extends XmlAttributeComponent {
  constructor(root) {
    super(root);
    this.xmlKeys = { type: 'w:fldCharType' };
  }
}
class FieldChar extends XmlComponent {
  constructor(type) {
    super('w:fldChar');
    this.root.push(new FieldCharAttributes({ type }));
  }
}
class SpaceAttributes extends XmlAttributeComponent {
  constructor(root) {
    super(root);
    this.xmlKeys = { space: 'xml:space' };
  }
}
class InstructionText extends XmlComponent {
  constructor(text) {
    super('w:instrText');
    this.root.push(new SpaceAttributes({ space: 'preserve' }));
    this.root.push(text);
  }
}
const fieldRun = (type) => new Run({ children: [new FieldChar(type)] });
const instructionRun = (text) => new Run({ children: [new InstructionText(text)] });

/** Heading levels 1-3, clickable entries, \u = also use paragraph outline levels. */
export const TOC_INSTRUCTION = ' TOC \\o "1-3" \\h \\z \\u ';
export const TOC_MAX_LEVEL = 3;

// ---------------------------------------------------------------- title page

function formatLongDate(isoDate) {
  return new Intl.DateTimeFormat('en-GB', { dateStyle: 'long', timeZone: 'UTC' }).format(
    new Date(`${isoDate}T00:00:00Z`),
  );
}

export function titlePage({ version, registry }) {
  const legal = registry.chapterEntry(LEGAL_CHAPTER_FILE);
  return [
    new Paragraph({ spacing: { before: 3600 }, children: [] }),
    new Paragraph({ style: 'Title', children: [new TextRun(GUIDE_TITLE)] }),
    new Paragraph({
      style: 'Subtitle',
      children: [new TextRun('A step-by-step guide for visitors and administrators')],
    }),
    new Paragraph({
      style: 'TitleMeta',
      children: [
        new TextRun(
          `Version of ${formatLongDate(version.isoDate)}. Screenshots taken from the app at commit ${version.commit}.`,
        ),
      ],
    }),
    new Paragraph({
      style: 'TitleMeta',
      children: [
        new TextRun(
          'The About, Contact, Privacy, Cookie, Terms and Affiliate disclosure pages are still drafts with placeholder text pending legal review (see ',
        ),
        new InternalHyperlink({
          anchor: legal.bookmark,
          children: [new TextRun({ text: legal.text, style: 'Hyperlink' })],
        }),
        new TextRun(').'),
      ],
    }),
  ];
}

// ---------------------------------------------------------------- contents

/**
 * The Contents page. The TOC field is marked for update on open (settings.xml), and its stored
 * result already lists every heading as a clickable link so viewers that never refresh fields
 * still show a usable index. Page numbers appear once Word refreshes the field.
 */
export function contentsPage(registry) {
  const entries = registry.entries.filter((entry) => entry.level <= TOC_MAX_LEVEL);
  const heading = new Paragraph({
    style: 'TOCHeading',
    children: [new Bookmark({ id: CONTENTS_BOOKMARK, children: [new TextRun('Contents')] })],
  });
  const hint = new Paragraph({
    style: 'Hint',
    children: [
      new TextRun(
        'Click an entry to jump to it. In Word, View > Navigation Pane shows a clickable outline of the whole guide. ' +
          'If page numbers are missing, right-click the contents and choose Update Field.',
      ),
    ],
  });
  const lines = entries.map((entry, index) => {
    const children = [];
    if (index === 0) {
      children.push(fieldRun('begin'), instructionRun(TOC_INSTRUCTION), fieldRun('separate'));
    }
    children.push(
      new InternalHyperlink({ anchor: entry.bookmark, children: [new TextRun(entry.text)] }),
    );
    return new Paragraph({ style: `TOC${entry.level}`, children });
  });
  const end = new Paragraph({ children: [fieldRun('end')] });
  return [heading, hint, ...lines, end];
}

// ---------------------------------------------------------------- header and footer

export function pageHeader() {
  return new Header({
    children: [
      new Paragraph({
        style: 'Header',
        tabStops: [{ type: TabStopType.RIGHT, position: TEXT_WIDTH_TW }],
        border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: COLORS.border, space: 4 } },
        children: [
          new TextRun(GUIDE_TITLE),
          new TextRun({ children: [new Tab()] }),
          // Current Heading 1 (part or chapter). Word fills it in on every page.
          new SimpleField('STYLEREF "Heading 1"', ''),
        ],
      }),
    ],
  });
}

export function pageFooter() {
  return new Footer({
    children: [
      new Paragraph({
        style: 'Footer',
        alignment: AlignmentType.CENTER,
        children: [new TextRun({ children: ['Page ', PageNumber.CURRENT, ' of ', PageNumber.TOTAL_PAGES] })],
      }),
    ],
  });
}

/** The first page (title) has no header or footer. */
export const emptyHeader = () => new Header({ children: [new Paragraph({ children: [] })] });
export const emptyFooter = () => new Footer({ children: [new Paragraph({ children: [] })] });
