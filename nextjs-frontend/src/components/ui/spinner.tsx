import * as React from 'react';

import { cn } from '@/lib/utils';

const SIZE_CLASSES = {
  sm: 'h-4 w-4',
  md: 'h-6 w-6',
  lg: 'h-10 w-10',
} as const;

export type SpinnerSize = keyof typeof SIZE_CLASSES;

type SpinnerProps = Omit<React.HTMLAttributes<HTMLSpanElement>, 'children'> & {
  size?: SpinnerSize;
  /** Accessible name and screen-reader text. */
  label?: string;
  /**
   * Draws the ring only: no role, no name, hidden from assistive technology. Use it inside a container
   * that is already the `role="status"` region, so the loading state is announced once.
   */
  decorative?: boolean;
};

/**
 * Loading spinner: a ring with a gap, drawn in `currentColor` (the theme's `--accent-ink`, readable on both
 * the light and the dark surfaces). It spins only under `motion-safe`; with reduced motion it stays a static
 * ring, still visible. The size comes from the `size` prop and never changes while it is shown.
 */
function Spinner({
  size = 'md',
  label = 'Loading',
  decorative = false,
  className,
  ...props
}: SpinnerProps) {
  const ring = (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      fill="none"
      className="h-full w-full motion-safe:animate-spin motion-reduce:animate-none"
    >
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="3" opacity="0.25" />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
  const classes = cn('inline-block shrink-0 text-accent-ink', SIZE_CLASSES[size], className);

  if (decorative) {
    return (
      <span aria-hidden="true" className={classes} {...props}>
        {ring}
      </span>
    );
  }

  return (
    <span role="status" aria-live="polite" aria-label={label} className={classes} {...props}>
      {ring}
      <span className="sr-only">{label}</span>
    </span>
  );
}

export { Spinner };
