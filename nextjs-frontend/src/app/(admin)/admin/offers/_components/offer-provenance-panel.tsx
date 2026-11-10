import { availableOfferActions, type OfferRowStatus } from '@/lib/admin-catalogue/offer-status';
import { providerLabel } from '@/lib/admin-catalogue/offer-labels';
import type { OfferProvenanceView } from '@/lib/admin-catalogue/offer-view';
import { formatAdminDate } from '../../_components/format-admin-date';
import { DismissSourceChangeButton } from './dismiss-source-change-button';

type OfferProvenancePanelProps = {
  provenance: OfferProvenanceView;
  offerId: string;
  offerTitle: string;
  status: OfferRowStatus;
  /** The review token of the offer as this page loaded it: the dismissal sends it back. */
  reviewToken: string;
};

/**
 * Where an imported offer came from, read only: the network, its id for the promotion, when the importer last
 * applied the source and, if the source has moved on since, when it was noticed. When it has, and the offer can
 * still be acted on (an archived offer or a status this page does not know cannot), the panel offers "Dismiss
 * source change". The API checks the admin's own permission; the page only knows what the status allows.
 */
export function OfferProvenancePanel({
  provenance,
  offerId,
  offerTitle,
  status,
  reviewToken,
}: OfferProvenancePanelProps) {
  const imported = formatAdminDate(provenance.appliedAt);
  const changed = provenance.sourceChangedAt ? formatAdminDate(provenance.sourceChangedAt) : '';
  const moved = provenance.sourceChangedAt !== null;
  const canDismiss = moved && availableOfferActions(status).length > 0;

  return (
    <section aria-labelledby="offer-provenance-heading" className="space-y-2">
      <h2 id="offer-provenance-heading" className="text-base font-semibold">
        Provenance
      </h2>
      <dl className="grid gap-x-6 gap-y-2 rounded-lg border p-3 text-sm sm:grid-cols-2">
        <div>
          <dt className="text-muted-foreground">Network</dt>
          <dd className="font-medium">{providerLabel(provenance.provider)}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">External ID</dt>
          <dd className="break-all font-medium">{provenance.externalId}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Last imported</dt>
          <dd className="font-medium">{imported || 'Unknown'}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Source changed</dt>
          <dd className="font-medium">{moved ? changed || 'Yes' : 'No'}</dd>
        </div>
      </dl>
      {moved ? (
        <p className="text-sm text-muted-foreground">
          The source of this offer has changed since it was imported. Its text here has not been
          changed to match: compare it with the source before you verify or publish it, or dismiss
          the change once you have.
        </p>
      ) : null}
      {canDismiss ? (
        <DismissSourceChangeButton
          offerId={offerId}
          offerTitle={offerTitle}
          reviewToken={reviewToken}
        />
      ) : null}
    </section>
  );
}
