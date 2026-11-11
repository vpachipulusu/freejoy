import { availableOfferActions, type OfferRowStatus } from '@/lib/admin-catalogue/offer-status';
import type { OfferDetailView } from '@/lib/admin-catalogue/offer-view';
import { buildProposalRows } from '@/lib/admin-catalogue/partner-proposal';
import { formatAdminDate } from '../../_components/format-admin-date';
import { PartnerUpdateReview } from './partner-update-review';

type PartnerSubmissionPanelProps = {
  offer: OfferDetailView;
  status: OfferRowStatus;
  /** Category names by id, for the comparison (an id with no name shows as "Unknown category"). */
  categoryNames: Record<string, string>;
};

/**
 * "Submitted by retailer": the merchant and the ref the retailer chose, when it was last sent, and, when an update
 * is waiting on an offer an editor has touched, the comparison with apply and dismiss. Renders nothing for an
 * offer that did not come through the partner API.
 */
export function PartnerSubmissionPanel({
  offer,
  status,
  categoryNames,
}: PartnerSubmissionPanelProps) {
  const partner = offer.partnerSubmission;
  if (!partner) return null;
  const pending = partner.pending;
  const rows = pending
    ? buildProposalRows(
        offer,
        pending.payload,
        (id) => categoryNames[id] ?? 'Unknown category',
        formatAdminDate
      )
    : [];

  return (
    <div className="space-y-4">
      <section aria-labelledby="partner-submission-heading" className="space-y-2">
        <h2 id="partner-submission-heading" className="text-base font-semibold">
          Submitted by retailer
        </h2>
        <dl className="grid gap-x-6 gap-y-2 rounded-lg border p-3 text-sm sm:grid-cols-3">
          <div>
            <dt className="text-muted-foreground">Merchant</dt>
            <dd className="break-words font-medium">{partner.merchantName}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Retailer ref</dt>
            <dd className="break-all font-medium">{partner.ref}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Last sent</dt>
            <dd className="font-medium">{formatAdminDate(partner.submittedAt) || 'Unknown'}</dd>
          </div>
        </dl>
        <p className="text-sm text-muted-foreground">
          This offer came through the partner API as a draft. Nothing a retailer sends is published
          until an editor has reviewed, verified and published it, and confirmed how it is paid for:
          the commercial basis shown is a placeholder.
        </p>
      </section>
      <PartnerUpdateReview
        offerId={offer.id}
        offerTitle={offer.title}
        reviewToken={offer.reviewToken}
        pendingHash={pending ? pending.hash : null}
        sentOn={pending ? formatAdminDate(pending.submittedAt) : ''}
        rows={rows}
        actionable={availableOfferActions(status).length > 0}
        live={status === 'PUBLISHED'}
      />
    </div>
  );
}
