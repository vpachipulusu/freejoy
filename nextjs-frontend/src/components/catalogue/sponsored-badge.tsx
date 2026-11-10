import { cn } from '@/lib/utils';

interface SponsoredBadgeProps {
  className?: string;
  /** Lets a card point its link at the badge (`aria-describedby`), so the word is read with the link. */
  id?: string;
}

/**
 * Marks a paid listing. The word is the signal (text, not colour alone) and it is in the reading order. The
 * visually hidden " listing" completes the name, so the badge reads "Sponsored listing" to a screen reader. No rule
 * hides it or cuts it short at any width (a unit test checks the classes; the 320px layout itself is not browser
 * verified).
 */
export function SponsoredBadge({ className, id }: SponsoredBadgeProps) {
  return (
    <span
      id={id}
      role="note"
      className={cn(
        'inline-flex min-h-6 items-center rounded-pill border border-foreground px-2.5 py-0.5 text-[11px] font-bold uppercase tracking-wide text-foreground',
        className
      )}
    >
      Sponsored<span className="sr-only"> listing</span>
    </span>
  );
}
