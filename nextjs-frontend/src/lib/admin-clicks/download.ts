/** Browser only: hands a CSV string to the visitor as a file download. */

const REVOKE_DELAY_MS = 1000;

/**
 * Saves `csv` as `filename`. The string already starts with the byte-order mark, which a Blob keeps as the UTF-8
 * bytes EF BB BF. The link is created, clicked and removed at once; the object URL is released a moment later so
 * the browser has started the download.
 */
export function saveCsvFile(filename: string, csv: string): void {
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.rel = 'noopener';
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), REVOKE_DELAY_MS);
}
