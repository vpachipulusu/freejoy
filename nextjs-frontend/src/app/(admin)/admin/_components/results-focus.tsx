'use client';

import { useEffect, useRef, type ReactNode } from 'react';

/**
 * The results heading, and where keyboard focus goes after the filters or the page change. Previous and Next
 * disappear on the first and last page, which would drop focus to the document body; moving focus to the heading
 * keeps the reader at the results. The first render does not move focus, and `focusKey` (the page's canonical
 * link) says when the results changed. The heading draws no focus style of its own: the single global
 * `*:focus-visible` outline in globals.css is the project's only focus indicator (see focus-outline-guard.test).
 * `level` is the heading level (an h2 by default; a section that already has its own h2 uses 3).
 */
export function ResultsFocus({
  focusKey,
  headingId,
  level = 2,
  children,
}: {
  focusKey: string;
  /** The heading's id, for a control that has to move focus here (Retry) or an `aria-labelledby`. */
  headingId?: string;
  level?: 2 | 3;
  children: ReactNode;
}) {
  const heading = useRef<HTMLHeadingElement>(null);
  const lastKey = useRef(focusKey);

  useEffect(() => {
    if (lastKey.current === focusKey) return;
    lastKey.current = focusKey;
    heading.current?.focus();
  }, [focusKey]);

  const Tag = level === 3 ? 'h3' : 'h2';
  return (
    <Tag
      ref={heading}
      id={headingId}
      tabIndex={-1}
      className="font-semibold leading-none tracking-tight"
    >
      {children}
    </Tag>
  );
}
