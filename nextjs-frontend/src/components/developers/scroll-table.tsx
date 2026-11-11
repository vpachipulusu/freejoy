import type { ReactNode } from 'react';

interface ScrollTableProps {
  /** Names the table, both as its caption (read by screen readers) and as its scroll region. */
  caption: string;
  headings: readonly string[];
  children: ReactNode;
}

/**
 * A data table with header cells. The wrapper scrolls sideways when the table is wider than the screen, takes
 * keyboard focus so it can be scrolled, and keeps the page itself from scrolling sideways.
 */
export function ScrollTable({ caption, headings, children }: ScrollTableProps) {
  return (
    <div
      role="region"
      aria-label={caption}
      tabIndex={0}
      className="mt-3 max-w-full overflow-x-auto rounded-card border border-border"
    >
      <table className="w-full min-w-[32rem] border-collapse text-start text-sm">
        <caption className="sr-only">{caption}</caption>
        <thead className="bg-secondary">
          <tr>
            {headings.map((heading) => (
              <th
                key={heading}
                scope="col"
                className="px-3 py-2 text-start font-bold text-foreground"
              >
                {heading}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="[&_td]:px-3 [&_td]:py-2 [&_td]:align-top [&_tr]:border-t [&_tr]:border-border">
          {children}
        </tbody>
      </table>
    </div>
  );
}
