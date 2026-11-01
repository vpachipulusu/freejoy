// Single place for paths, the page list and layout numbers.
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));

/** docs/user-guide (the folder that holds the Markdown pages and images/). */
export const GUIDE_DIR = path.resolve(here, '..', '..');
export const OUTPUT_FILE = path.join(GUIDE_DIR, 'Freejoy-User-Guide.docx');

/** The index page. Its intro and "Please read this first" section become the preface. */
export const INDEX_FILE = 'README.md';
export const PREFACE_SECTION = 'Please read this first';
/** A paragraph of the index page that starts like this points at the Word file and is left out of it. */
export const WORD_NOTE_PREFIX = 'Word version:';
/** Sections of the index page that are deliberately not part of the Word guide. */
export const SKIPPED_INDEX_SECTIONS = ['Developer documentation'];

/**
 * The parts of the guide, in order. `indexSection` is the exact "##" heading in README.md
 * whose table becomes the part's overview; `files` are the chapters in reading order.
 */
export const PARTS = [
  {
    number: 1,
    indexSection: 'For visitors (the public site)',
    files: [
      '01-home-and-navigation.md',
      '02-browse-and-search.md',
      '03-offer-details.md',
      '04-email-alerts.md',
      '05-information-pages.md',
      '06-keyboard-and-accessibility.md',
    ],
  },
  {
    number: 2,
    indexSection: 'For administrators (the admin area)',
    files: [
      'admin-01-signing-in.md',
      'admin-02-offers.md',
      'admin-03-categories-and-merchants.md',
      'admin-04-users-and-2fa.md',
      'admin-05-subscriptions-and-analytics.md',
      'admin-06-audit-logs-seo-settings.md',
    ],
  },
];

/** The chapter that explains the draft legal pages (linked from the title page note). */
export const LEGAL_CHAPTER_FILE = '05-information-pages.md';

export const GUIDE_TITLE = 'Freejoy User Guide';
export const MAX_OUTPUT_BYTES = 20 * 1024 * 1024;

// ---- Page layout (A4, 2 cm margins). 1 cm = 567 twips (1/20 pt).
export const TWIPS_PER_CM = 567;
export const PAGE = {
  widthTw: 11906,
  heightTw: 16838,
  marginTw: 1134,
};
export const TEXT_WIDTH_TW = PAGE.widthTw - 2 * PAGE.marginTw; // 9638 (17.0 cm)
export const TEXT_WIDTH_CM = TEXT_WIDTH_TW / TWIPS_PER_CM;

// ---- Image limits (cm). Real pixel aspect ratio is always kept.
export const IMAGE = {
  desktopMaxWidthCm: 16,
  mobileMaxWidthCm: 6,
  /** Desktop shots narrower than the 1440 px reference scale down in proportion, but not below this. */
  desktopMinWidthCm: 8,
  desktopReferencePx: 1440,
  /** Tallest one picture may be. Taller screenshots are cut into equal slices of at most this height. */
  maxHeightCm: 22,
  /** A too-tall shot may shrink to fit a page only down to this fraction of its width; below that it is sliced. */
  minShrinkDesktop: 0.85,
  minShrinkMobile: 0.85,
  /** Side-by-side desktop + phone pair: width of the two table cells. */
  pairDesktopCellCm: 10.6,
  pairMobileCellCm: TEXT_WIDTH_CM - 10.6,
  cellPaddingCm: 0.3,
};

/** Everything that identifies one guide. Tests build small fixture guides by passing their own. */
export const defaultConfig = {
  guideDir: GUIDE_DIR,
  outputFile: OUTPUT_FILE,
  indexFile: INDEX_FILE,
  prefaceSection: PREFACE_SECTION,
  skippedSections: SKIPPED_INDEX_SECTIONS,
  parts: PARTS,
  legalChapterFile: LEGAL_CHAPTER_FILE,
};
