/**
 * Saving the CSV: a Blob of UTF-8 text that keeps the byte-order mark, offered through a temporary link whose
 * download name is the file name, with the object URL released afterwards.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { saveCsvFile } from '@/lib/admin-clicks/download';

describe('saveCsvFile', () => {
  let createObjectURL: ReturnType<typeof vi.fn>;
  let revokeObjectURL: ReturnType<typeof vi.fn>;
  let clicked: HTMLAnchorElement | undefined;
  let inDocumentWhenClicked: boolean | undefined;

  beforeEach(() => {
    vi.useFakeTimers();
    createObjectURL = vi.fn(() => 'blob:fake');
    revokeObjectURL = vi.fn();
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL, revokeObjectURL }));
    clicked = undefined;
    inDocumentWhenClicked = undefined;
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {
      const link = document.querySelector<HTMLAnchorElement>('a[download]');
      clicked = link ?? undefined;
      inDocumentWhenClicked = link !== null;
    });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('downloads a UTF-8 CSV blob under the given name and removes the link again', () => {
    saveCsvFile('report.csv', 'a,b\r\n');

    const blob = createObjectURL.mock.calls[0][0] as Blob;
    expect(blob.type).toBe('text/csv;charset=utf-8');
    expect(blob.size).toBe(5);
    expect(clicked?.download).toBe('report.csv');
    expect(clicked?.getAttribute('href')).toBe('blob:fake');
    expect(inDocumentWhenClicked).toBe(true);
    expect(document.body.contains(clicked as Node)).toBe(false);
  });

  it('keeps the byte-order mark as the three UTF-8 bytes', async () => {
    vi.useRealTimers(); // FileReader needs real timers in jsdom
    saveCsvFile('report.csv', `${String.fromCharCode(0xfeff)}x`);

    const blob = createObjectURL.mock.calls[0][0] as Blob;
    const bytes = await new Promise<Uint8Array>((resolve) => {
      const reader = new FileReader();
      reader.onload = () => resolve(new Uint8Array(reader.result as ArrayBuffer));
      reader.readAsArrayBuffer(blob);
    });
    expect([...bytes]).toEqual([0xef, 0xbb, 0xbf, 0x78]);
  });

  it('releases the object URL a moment later', () => {
    saveCsvFile('report.csv', 'x');

    expect(revokeObjectURL).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1000);
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:fake');
  });
});
