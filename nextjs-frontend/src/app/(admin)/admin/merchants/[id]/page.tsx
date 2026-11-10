import Link from 'next/link';
import { notFound } from 'next/navigation';
import { ArrowLeft } from 'lucide-react';
import { StatusPill } from '@/components/ui/status-pill';
import { merchantToFormValues } from '@/lib/admin-catalogue/merchant-schema';
import {
  catalogueErrorMessage,
  MERCHANT_NOT_FOUND_MESSAGE,
} from '@/lib/api/admin-catalogue-errors';
import { fetchAdminMerchant, toMerchantView } from '@/lib/api/admin-merchants';
import { logCatalogueFailure } from '@/lib/api/admin-catalogue-log';
import { InlineError } from '../../_components/inline-error';
import { requireAdminPage } from '../../_lib/require-admin-page';
import { MerchantForm } from '../merchant-form';
import { PartnerKeysSection } from '../_components/partner-keys-section';

type Params = Promise<{ id: string }>;

const RESOURCE_ID = /^[A-Za-z0-9_-]{1,128}$/;

export const metadata = { title: 'Edit merchant' };

export default async function EditMerchantPage({ params }: { params: Params }) {
  await requireAdminPage();
  const { id } = await params;
  if (!RESOURCE_ID.test(id)) notFound();

  const result = await fetchAdminMerchant(id);
  if (!result.success && result.status === 404) notFound();
  if (!result.success) {
    logCatalogueFailure('Admin merchant page: read failed', result);
    return (
      <InlineError
        message={catalogueErrorMessage(result, MERCHANT_NOT_FOUND_MESSAGE)}
        retryHref={`/admin/merchants/${id}`}
      />
    );
  }

  const merchant = toMerchantView(result.data);
  return (
    <div className="space-y-6">
      <div className="space-y-3">
        <Link
          href="/admin/merchants"
          className="inline-flex min-h-11 items-center gap-2 text-sm font-medium text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft aria-hidden="true" className="size-4" />
          Back to merchants
        </Link>
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="break-words text-3xl font-bold tracking-tight">{merchant.name}</h1>
          <StatusPill variant={merchant.active ? 'good' : 'neutral'}>
            {merchant.active ? 'Active' : 'Inactive'}
          </StatusPill>
        </div>
      </div>
      <MerchantForm
        mode="edit"
        merchantId={merchant.id}
        initialValues={merchantToFormValues(merchant)}
      />
      <PartnerKeysSection
        merchantId={merchant.id}
        merchantName={merchant.name}
        website={merchant.website}
        active={merchant.active}
      />
    </div>
  );
}
