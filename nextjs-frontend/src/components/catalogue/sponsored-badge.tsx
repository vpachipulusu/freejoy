import { cn } from '@/lib/utils';

interface SponsoredBadgeProps {
  className?: string;
}

/**
 * Marks a paid listing. The word is the signal (text, not colour alone) and it is in the reading order, so a
 * screen reader announces it with the offer; the label spells out what it means. Never hidden and never
 * conditional on a screen size: it stays visible at 320px wide.
 */
export function SponsoredBadge({ className }: SponsoredBadgeProps) {
  return (
    <span
      role="note"
      aria-label="Sponsored listing"
      className={cn(
        'inline-flex min-h-6 items-center rounded-pill border border-foreground px-2.5 py-0.5 text-[11px] font-bold uppercase tracking-wide text-foreground',
        className
      )}
    >
      Sponsored
    </span>
  );
}
