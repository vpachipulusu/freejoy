'use client';

import { Heart } from 'lucide-react';
import { toggleSaved } from '@/lib/saved/saved-store';
import { useIsSaved } from '@/lib/saved/use-saved';
import { cn } from '@/lib/utils';

interface SaveButtonProps {
  /** The offer's slug: the only thing that is stored. */
  slug: string;
  /** Names the button for screen readers ("Save {title}"); never stored. */
  title: string;
  /** Where to put it: absolute position inside the offer art, set by the card or hero. */
  className?: string;
}

/**
 * The heart (design screens 05 to 07): saves an offer on this device. A real button with `aria-pressed`,
 * a 44px target, and a heart that is filled when saved and outlined when not, so the state is never only
 * a colour. The one global focus outline applies (nothing here switches it off). It is a sibling of the
 * card link, never inside it, and `z-10` lifts it above the link's stretched click area. The server render
 * and the first client render both say "not saved"; the saved state arrives right after hydration without
 * moving anything, because the button is the same size either way.
 */
export function SaveButton({ slug, title, className }: SaveButtonProps) {
  const saved = useIsSaved(slug);

  return (
    <button
      type="button"
      aria-pressed={saved}
      aria-label={saved ? `Remove ${title} from saved` : `Save ${title}`}
      onClick={() => toggleSaved(slug)}
      className={cn(
        'group relative z-10 inline-flex h-11 w-11 items-center justify-center rounded-pill text-foreground motion-safe:transition-transform motion-safe:active:scale-90',
        className
      )}
    >
      {/* The tap target is 44px; the white disc the visitor sees is 32px, as in the design, so it fits a narrow card. */}
      <span className="flex h-8 w-8 items-center justify-center rounded-pill border border-border bg-card shadow-sm group-hover:bg-muted">
        <Heart
          aria-hidden="true"
          className={cn('h-4 w-4', saved ? 'fill-cta stroke-foreground' : 'fill-transparent')}
        />
      </span>
    </button>
  );
}
