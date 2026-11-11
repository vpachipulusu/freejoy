import { useId } from 'react';
import type { ProposalRow } from '@/lib/admin-catalogue/partner-proposal';

/** What a retailer sent can be one long unbroken string (a link above all): it wraps anywhere instead of overflowing. */
const VALUE_CLASS = 'whitespace-pre-wrap [overflow-wrap:anywhere]';

/** One line is a paragraph; only several lines are a list, so a screen reader does not announce "list, 1 item". */
function Values({ lines }: { lines: string[] }) {
  if (lines.length === 1) return <p className={VALUE_CLASS}>{lines[0]}</p>;
  return (
    <ul className="space-y-1">
      {lines.map((line, index) => (
        <li key={`${index}-${line}`} className={VALUE_CLASS}>
          {line}
        </li>
      ))}
    </ul>
  );
}

type ProposalRowsProps = { rows: ProposalRow[] };

/**
 * Each changed field as a heading and two named groups, Current and Proposed, side by side from 640px and stacked
 * below it. A group is named by the field's heading and its own visible label, so nothing is said twice.
 */
export function ChangedFields({ rows }: ProposalRowsProps) {
  const prefix = useId();
  return (
    <ul className="space-y-3">
      {rows.map((row) => {
        const headingId = `${prefix}-${row.key}`;
        return (
          <li key={row.key} className="space-y-2 rounded-lg border p-3 text-sm">
            <h3 id={headingId} className="font-medium">
              {row.label}
            </h3>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {(
                [
                  ['current', 'Current', row.current],
                  ['proposed', 'Proposed', row.proposed],
                ] as const
              ).map(([side, label, lines]) => (
                <div
                  key={side}
                  role="group"
                  aria-labelledby={`${headingId} ${headingId}-${side}`}
                  className="min-w-0 space-y-1"
                >
                  <p
                    id={`${headingId}-${side}`}
                    className="text-xs font-medium uppercase text-muted-foreground"
                  >
                    {label}
                  </p>
                  <Values lines={lines} />
                </div>
              ))}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

/** What applying will not do (the retailer's link on an offer without exactly one direct link), as plain text. */
export function NotAppliedNotes({ rows }: ProposalRowsProps) {
  const notes = rows.filter((row) => row.note !== undefined);
  if (notes.length === 0) return null;
  return (
    <div className="space-y-2 text-sm">
      {notes.map((row) => (
        <div key={row.key} className="space-y-1">
          <p className="font-medium">{row.note}</p>
          <p className="text-muted-foreground [overflow-wrap:anywhere]">
            Link sent (shown as text): {row.proposed[0]}
          </p>
        </div>
      ))}
    </div>
  );
}
