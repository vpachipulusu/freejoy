// Images: reading, sizing and the "Figure N. ..." numbering.
import fs from 'node:fs';
import path from 'node:path';
import { IMAGE } from './config.mjs';

const PNG_SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
const PX_PER_CM = 96 / 2.54;

/** Pixel size from the PNG header (IHDR chunk). */
export function readPngSize(buffer, label = 'image') {
  if (buffer.length < 24 || !buffer.subarray(0, 8).equals(PNG_SIGNATURE)) {
    throw new Error(`${label} is not a PNG file`);
  }
  return { width: buffer.readUInt32BE(16), height: buffer.readUInt32BE(20) };
}

export const isMobileShot = (src) => /-m\.png$/i.test(src);
export const isDesktopShot = (src) => /-d\.png$/i.test(src);
/** "images/home-d.png" and "images/home-m.png" -> "images/home" */
export const shotBase = (src) => src.replace(/-[dm]\.png$/i, '');

/**
 * Page layout of one screenshot. Phone shots get up to 6 cm of width, desktop shots up to 16 cm
 * (less when the capture itself is narrower than the 1440 px reference, never under 8 cm), aspect
 * ratio untouched. `widthCapCm` lowers the width limit (used inside the side-by-side table).
 *
 * A screenshot taller than one page (IMAGE.maxHeightCm) is first shrunk to fit, but only while it
 * keeps at least IMAGE.minShrink of its width. Beyond that it would become unreadable, so it is cut
 * into equal slices that are each at most one page tall and keep the full width. Every slice is the
 * same embedded picture cropped with top/bottom percentages.
 */
export function layoutImage(src, size, widthCapCm = Infinity) {
  let widthCm;
  if (isMobileShot(src)) {
    widthCm = IMAGE.mobileMaxWidthCm;
  } else if (size.width >= IMAGE.desktopReferencePx) {
    widthCm = IMAGE.desktopMaxWidthCm;
  } else {
    widthCm = Math.max(
      (IMAGE.desktopMaxWidthCm * size.width) / IMAGE.desktopReferencePx,
      IMAGE.desktopMinWidthCm,
    );
  }
  widthCm = Math.min(widthCm, widthCapCm);
  const heightCm = (widthCm * size.height) / size.width;
  const shrink = IMAGE.maxHeightCm / heightCm;
  const minShrink = isMobileShot(src) ? IMAGE.minShrinkMobile : IMAGE.minShrinkDesktop;
  if (heightCm <= IMAGE.maxHeightCm || shrink >= minShrink) {
    const scale = Math.min(1, shrink);
    const fitted = {
      part: 1,
      topPct: 0,
      bottomPct: 0,
      heightCm: heightCm * scale,
      heightPx: Math.round(heightCm * scale * PX_PER_CM),
    };
    return {
      widthCm: widthCm * scale,
      heightCm: heightCm * scale,
      widthPx: Math.round(widthCm * scale * PX_PER_CM),
      count: 1,
      slices: [fitted],
    };
  }
  const count = Math.ceil(heightCm / IMAGE.maxHeightCm - 1e-9);
  const sliceCm = heightCm / count;
  const slices = Array.from({ length: count }, (_, k) => ({
    part: k + 1,
    topPct: (100 * k) / count,
    bottomPct: (100 * (count - k - 1)) / count,
    heightCm: sliceCm,
    heightPx: Math.round(sliceCm * PX_PER_CM),
  }));
  return { widthCm, heightCm, widthPx: Math.round(widthCm * PX_PER_CM), count, slices };
}

/** Loads an image referenced from a page; throws a clear error when it is missing. */
export function loadImage(src, pageDir, where) {
  const file = path.resolve(pageDir, src);
  if (!fs.existsSync(file)) {
    throw new Error(`Missing image "${src}" referenced at ${where} (expected ${file})`);
  }
  if (!/\.png$/i.test(file)) {
    throw new Error(`Unsupported image type "${src}" at ${where}: only PNG is handled`);
  }
  const data = fs.readFileSync(file);
  return { file, data, size: readPngSize(data, src) };
}
