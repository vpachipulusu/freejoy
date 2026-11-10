import { Download, RefreshCw } from 'lucide-react';
import { StatusPill } from '@/components/ui/status-pill';

type OfferImportBadgeProps = {
  importProvider: string | null;
  sourceChangedAt: string | null;
};

/**
 * A small badge under an offer's title in the list: "Source changed" when the network's source moved on and
 * nobody has reviewed it (that wins), "Imported" for any other imported offer, nothing for an offer made by hand.
 * The words carry the meaning; the icon is decorative and the tint only adds to them.
 */
export function OfferImportBadge({ importProvider, sourceChangedAt }: OfferImportBadgeProps) {
  if (sourceChangedAt) {
    return (
      <StatusPill variant="warn" icon={RefreshCw} data-testid="offer-import-badge" className="mt-1">
        Source changed
      </StatusPill>
    );
  }
  if (importProvider) {
    return (
      <StatusPill
        variant="neutral"
        icon={Download}
        data-testid="offer-import-badge"
        className="mt-1"
      >
        Imported
      </StatusPill>
    );
  }
  return null;
}
