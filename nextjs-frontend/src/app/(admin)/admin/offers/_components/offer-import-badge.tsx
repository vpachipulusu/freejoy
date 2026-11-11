import { Download, RefreshCw, Store } from 'lucide-react';
import { StatusPill } from '@/components/ui/status-pill';

type OfferImportBadgeProps = {
  importProvider: string | null;
  sourceChangedAt: string | null;
  /** The retailer's own ref, for an offer submitted through the partner API. */
  partnerRef?: string | null;
};

/**
 * A small badge under an offer's title in the list: "Source changed" when the network's source moved on and
 * nobody has reviewed it (that wins), "Imported" for any other imported offer, nothing for an offer made by hand.
 * The words carry the meaning; the icon is decorative and the tint only adds to them.
 */
export function OfferImportBadge({
  importProvider,
  sourceChangedAt,
  partnerRef = null,
}: OfferImportBadgeProps) {
  if (importProvider === 'partner') {
    const waiting = sourceChangedAt !== null;
    return (
      <StatusPill
        variant={waiting ? 'warn' : 'neutral'}
        icon={Store}
        data-testid="offer-import-badge"
        className="mt-1"
      >
        {waiting ? 'Retailer update waiting' : 'Submitted by retailer'}
        {partnerRef ? ` · ref ${partnerRef}` : ''}
      </StatusPill>
    );
  }
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
