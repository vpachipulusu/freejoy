import Link from 'next/link';
import { notFound } from 'next/navigation';
import { ArrowLeft } from 'lucide-react';
import { hasOnlyDirectLinks, offerToFormValues } from '@/lib/admin-catalogue/offer-view';
import { catalogueErrorMessage, OFFER_NOT_FOUND_MESSAGE } from '@/lib/api/admin-catalogue-errors';
import { fetchAdminOffer, toOfferDetail } from '@/lib/api/admin-offers';
import { logCatalogueFailure } from '@/lib/api/admin-catalogue-log';
import { formatAdminDate } from '../../_components/format-admin-date';
import { InlineError } from '../../_components/inline-error';
import {
  loadCategoryOptions,
  loadMerchantOptions,
  TRUNCATED_NOTICE,
  type PickOption,
} from '../../_lib/pick-lists';
import { requireAdminPage } from '../../_lib/require-admin-page';
import { DirtyFormProvider } from '../../_components/dirty-form-context';
import { OfferStatusPill } from '../_components/offer-status-pill';
import { OfferForm } from '../_components/offer-form';
import { OfferLifecycleButtons } from '../_components/offer-lifecycle-buttons';
import { OfferLinksList } from '../_components/offer-links-list';
import { OfferProvenancePanel } from '../_components/offer-provenance-panel';
import { OfferReviewProvider } from '../_components/offer-review-context';

type Params = Promise<{ id: string }>;

function withCurrent(options: PickOption[], id: string, fallbackName: string): PickOption[] {
  return options.some((option) => option.id === id)
    ? options
    : [{ id, name: fallbackName }, ...options];
}

const OFFER_ID = /^[A-Za-z0-9_-]{1,128}$/;

export const metadata = { title: 'Edit offer' };

export default async function EditOfferPage({ params }: { params: Params }) {
  await requireAdminPage();
  const { id } = await params;
  if (!OFFER_ID.test(id)) notFound();

  const [result, categories, merchants] = await Promise.all([
    fetchAdminOffer(id),
    loadCategoryOptions(),
    loadMerchantOptions(),
  ]);
  if (!result.success && result.status === 404) notFound();

  const retryHref = `/admin/offers/${id}`;
  if (!result.success) {
    logCatalogueFailure('Admin offer page: read failed', result);
    return (
      <InlineError
        message={catalogueErrorMessage(result, OFFER_NOT_FOUND_MESSAGE)}
        retryHref={retryHref}
      />
    );
  }
  if (!categories.ok || !merchants.ok) {
    return (
      <InlineError
        message={!categories.ok ? categories.message : merchants.ok ? '' : merchants.message}
        retryHref={retryHref}
      />
    );
  }

  const offer = toOfferDetail(result.data);
  // A pick list holds at most 100 rows: keep the offer's own category and merchant selectable regardless.
  const categoryOptions = withCurrent(categories.options, offer.categoryId, 'Current category');
  const merchantOptions = withCurrent(merchants.options, offer.merchantId, 'Current merchant');
  const verified = offer.lastVerifiedAt ? formatAdminDate(offer.lastVerifiedAt) : '';

  return (
    <DirtyFormProvider>
      <OfferReviewProvider>
        <div className="space-y-6">
          <div className="space-y-3">
            <Link
              href="/admin/offers"
              className="inline-flex min-h-11 items-center gap-2 text-sm font-medium text-muted-foreground hover:text-foreground"
            >
              <ArrowLeft aria-hidden="true" className="size-4" />
              Back to freebies
            </Link>
            <div
              role="group"
              aria-label="Offer title and status"
              className="flex flex-wrap items-center gap-3"
            >
              <h1 className="break-words text-3xl font-bold tracking-tight">{offer.title}</h1>
              <OfferStatusPill status={offer.status} />
            </div>
            <p className="text-sm text-muted-foreground">
              {verified
                ? `Last verified ${verified}.`
                : 'Not verified yet. Verify it before publishing.'}
            </p>
            <OfferLifecycleButtons
              offerId={offer.id}
              offerTitle={offer.title}
              status={offer.status}
              reviewToken={offer.reviewToken}
              commercialBasis={offer.commercialBasis}
              confirmBasisToPublish={hasOnlyDirectLinks(offer.links)}
            />
          </div>

          {offer.provenance ? (
            <OfferProvenancePanel
              provenance={offer.provenance}
              offerId={offer.id}
              offerTitle={offer.title}
              status={offer.status}
              reviewToken={offer.reviewToken}
            />
          ) : null}

          <OfferLinksList links={offer.links} />

          {categories.truncated || merchants.truncated ? (
            <p className="text-sm text-muted-foreground">{TRUNCATED_NOTICE}</p>
          ) : null}

          <OfferForm
            mode="edit"
            offerId={offer.id}
            status={offer.status}
            initialValues={offerToFormValues(offer)}
            categories={categoryOptions}
            merchants={merchantOptions}
            readOnly={offer.status === 'ARCHIVED' || offer.status === 'UNKNOWN'}
          />
        </div>
      </OfferReviewProvider>
    </DirtyFormProvider>
  );
}
