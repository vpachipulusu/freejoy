import { cn } from '@/lib/utils';
import { catalogueErrorMessage } from '@/lib/api/admin-catalogue-errors';
import { fetchOfferStatusCounts } from '@/lib/api/admin-offers';
import { logCatalogueFailure } from '@/lib/api/admin-catalogue-log';
import { InlineError } from '../_components/inline-error';
import { OFFERS_PATH } from './offers-query';

const STATS = [
  { key: 'TOTAL', label: 'Total offers', tone: '' },
  { key: 'PUBLISHED', label: 'Published', tone: 'bg-good text-good-foreground' },
  { key: 'DRAFT', label: 'Drafts', tone: '' },
  { key: 'EXPIRED', label: 'Expired', tone: 'bg-warn text-warn-foreground' },
  { key: 'ARCHIVED', label: 'Archived', tone: '' },
  // Imported offers that are drafts or whose source changed: it overlaps the statuses, so it is not in the total.
  { key: 'NEEDS_REVIEW', label: 'Needs review', tone: '' },
] as const;

/** Offer counts straight from the API's list totals. If any count cannot be read, none is shown. */
export async function OfferStatsStrip() {
  const result = await fetchOfferStatusCounts();
  if (!result.success) {
    logCatalogueFailure('Admin offers page: counts failed', result);
    return (
      <InlineError
        message={catalogueErrorMessage(result, 'The offer counts could not be loaded.')}
        retryHref={OFFERS_PATH}
      />
    );
  }

  return (
    <dl aria-label="Offer counts" className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
      {STATS.map(({ key, label, tone }) => (
        // The term comes first for assistive technology; the number is shown above it.
        <div
          key={key}
          className={cn('flex flex-col-reverse rounded-card border bg-card p-4', tone)}
        >
          <dt className="text-sm">{label}</dt>
          <dd className="text-3xl font-bold tabular-nums">{result.data[key]}</dd>
        </div>
      ))}
    </dl>
  );
}

export function OfferStatsSkeleton() {
  return (
    <div aria-busy="true" className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
      <p role="status" className="sr-only">
        Loading offer counts
      </p>
      {STATS.map(({ key }) => (
        <div key={key} className="h-[88px] animate-pulse rounded-card border bg-secondary" />
      ))}
    </div>
  );
}
