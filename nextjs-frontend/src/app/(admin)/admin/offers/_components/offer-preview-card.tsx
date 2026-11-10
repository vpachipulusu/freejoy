'use client';

import Link from 'next/link';
import { useWatch, type Control } from 'react-hook-form';
import { ExternalLink } from 'lucide-react';
import { OfferArt } from '@/components/catalogue/offer-art';
import { SponsoredBadge } from '@/components/catalogue/sponsored-badge';
import { offerTypeLabel } from '@/lib/admin-catalogue/offer-labels';
import { poundsToMinor, type OfferFormValues } from '@/lib/admin-catalogue/offer-schema';
import { publicOfferPath, type OfferRowStatus } from '@/lib/admin-catalogue/offer-status';
import type { Tint } from '@/lib/catalogue/types';
import type { PickOption } from './offer-form-fields';
import { OfferStatusPill } from './offer-status-pill';

type OfferPreviewCardProps = {
  control: Control<OfferFormValues>;
  merchants: PickOption[];
  /** Absent while creating: nothing exists to link to yet. */
  status?: OfferRowStatus;
  slug?: string;
};

function postageText(kind: string, amount: string): string {
  if (kind === 'FREE') return 'Free postage';
  if (kind === 'CHECK_TERMS') return 'Check the postage terms';
  const minor = poundsToMinor(amount.trim());
  return minor === null ? 'Postage costs money' : `Postage £${(minor / 100).toFixed(2)}`;
}

/**
 * How the offer will look on a card, drawn from the form as it is typed. A draft is never public, so this card
 * is the only preview; the link to the live page appears only once the offer is published.
 */
export function OfferPreviewCard({ control, merchants, status, slug }: OfferPreviewCardProps) {
  const values = useWatch({ control });
  const merchant = merchants.find((option) => option.id === values.merchantId);
  const publicPath = status && slug ? publicOfferPath(status, slug) : null;

  return (
    <aside
      aria-labelledby="offer-preview-heading"
      className="space-y-3 rounded-card border bg-card p-4"
    >
      <div className="flex items-center justify-between gap-2">
        <h2 id="offer-preview-heading" className="text-sm font-semibold">
          Card preview
        </h2>
        {status ? <OfferStatusPill status={status} /> : null}
      </div>
      <OfferArt
        image={{
          tint: (values.imageTint || 'blush') as Tint,
          label: values.imageLabel || 'label',
          alt: values.imageAlt || 'No picture description yet',
        }}
      />
      <p className="text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
        {merchant?.name ?? 'Merchant not chosen'}
        {values.offerType ? ` · ${offerTypeLabel(values.offerType)}` : ''}
      </p>
      {values.commercialBasis === 'SPONSORED' ? (
        <div>
          <SponsoredBadge />
        </div>
      ) : null}
      <p className="font-display text-base font-bold leading-snug">
        {values.title || 'Title not written yet'}
      </p>
      <p className="text-sm text-muted-foreground">
        {values.summary || 'Summary not written yet.'}
      </p>
      <p className="text-xs text-muted-foreground">
        {postageText(values.postageKind ?? 'FREE', values.postageAmount ?? '')}
      </p>
      {publicPath ? (
        <Link
          href={publicPath}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex min-h-11 items-center gap-2 text-sm font-semibold text-accent-ink underline underline-offset-4"
        >
          View the live page
          <ExternalLink aria-hidden="true" className="size-4" />
          <span className="sr-only">(opens in a new tab)</span>
        </Link>
      ) : (
        <p className="text-sm text-muted-foreground">
          {status
            ? 'Not public. The live page appears here once the offer is published.'
            : 'Not saved yet. Nothing here is public.'}
        </p>
      )}
    </aside>
  );
}
