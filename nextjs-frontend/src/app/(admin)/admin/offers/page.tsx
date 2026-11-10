import Link from 'next/link';
import { Suspense } from 'react';
import { Plus, Search } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { OFFER_STATUS_LABELS, type OfferStatus } from '@/lib/admin-catalogue/offer-status';
import { cn } from '@/lib/utils';
import { NativeSelect } from '../_components/native-select';
import { TableSkeleton } from '../_components/table-skeleton';
import { loadCategoryOptions } from '../_lib/pick-lists';
import { requireAdminPage } from '../_lib/require-admin-page';
import { OffersResults } from './offers-results';
import { OFFERS_COLUMNS } from './offers-table';
import { OfferStatsSkeleton, OfferStatsStrip } from './offers-stats';
import {
  MAX_QUERY_LENGTH,
  offersHref,
  OFFERS_PATH,
  parseOffersQuery,
  REVIEW_NEEDED,
} from './offers-query';

type SearchParams = Promise<{
  q?: string;
  status?: string;
  category?: string;
  review?: string;
  page?: string;
}>;

/** The chip order of the design: Published first, then Draft and Expired; Archived, then the review queue. */
const CHIP_STATUSES: readonly OfferStatus[] = ['PUBLISHED', 'DRAFT', 'EXPIRED', 'ARCHIVED'];

const CHIP_BASE =
  'inline-flex min-h-11 items-center rounded-full border px-4 text-sm font-semibold transition-colors hover:bg-secondary';
const CHIP_CURRENT = 'border-accent-ink bg-good text-good-foreground';

export const metadata = { title: 'Freebies' };

export default async function AdminOffersPage({ searchParams }: { searchParams: SearchParams }) {
  await requireAdminPage();
  const query = parseOffersQuery(await searchParams);
  const categories = await loadCategoryOptions();
  // The chips replace one another: a status (or All) leaves the review queue and the queue leaves the status.
  const chips = [
    { label: 'All', status: undefined, review: false },
    ...CHIP_STATUSES.map((status) => ({
      label: OFFER_STATUS_LABELS[status],
      status,
      review: false,
    })),
    { label: 'Needs review', status: undefined, review: true },
  ];

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Freebies</h1>
          <p className="text-muted-foreground">Curate, verify and publish freebie listings</p>
        </div>
        <Button asChild variant="accent">
          <Link href="/admin/offers/new">
            <Plus aria-hidden="true" />
            Add freebie
          </Link>
        </Button>
      </div>

      <Suspense fallback={<OfferStatsSkeleton />}>
        <OfferStatsStrip />
      </Suspense>

      <Card>
        <CardContent className="space-y-4 pt-6">
          <nav aria-label="Filter by status" className="flex flex-wrap gap-2">
            {chips.map(({ label, status, review }) => {
              const current = review ? query.review : !query.review && query.status === status;
              return (
                <Link
                  key={label}
                  href={offersHref({ ...query, status, review, page: 1 })}
                  aria-current={current ? 'true' : undefined}
                  className={cn(CHIP_BASE, current && CHIP_CURRENT)}
                >
                  {label}
                </Link>
              );
            })}
          </nav>

          <form
            action={OFFERS_PATH}
            method="get"
            role="search"
            className="grid gap-3 sm:grid-cols-[1fr_14rem_auto]"
          >
            {query.status ? (
              <input type="hidden" name="status" value={query.status.toLowerCase()} />
            ) : null}
            {query.review ? <input type="hidden" name="review" value={REVIEW_NEEDED} /> : null}
            <div className="space-y-2">
              <Label htmlFor="offer-search">Search offers by title, slug or summary</Label>
              <div className="relative">
                <Search
                  aria-hidden="true"
                  className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
                />
                <Input
                  key={query.q ?? ''}
                  id="offer-search"
                  type="search"
                  name="q"
                  className="pl-10"
                  maxLength={MAX_QUERY_LENGTH}
                  defaultValue={query.q}
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label htmlFor="offer-category-filter">Category</Label>
              <NativeSelect
                key={query.category ?? ''}
                id="offer-category-filter"
                name="category"
                defaultValue={query.category ?? ''}
              >
                <option value="">All categories</option>
                {categories.ok
                  ? categories.options.map((option) => (
                      <option key={option.id} value={option.id}>
                        {option.name}
                      </option>
                    ))
                  : null}
              </NativeSelect>
            </div>
            <div className="flex items-end">
              <Button type="submit" variant="outline" className="w-full sm:w-auto">
                Apply
              </Button>
            </div>
          </form>
          {categories.ok ? (
            categories.truncated ? (
              <p className="text-sm text-muted-foreground">
                The category filter lists only the first 100 categories.
              </p>
            ) : null
          ) : (
            <p role="alert" className="text-sm text-destructive">
              The category filter could not be loaded.
            </p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardContent className="pt-6">
          <Suspense
            key={JSON.stringify(query)}
            fallback={<TableSkeleton columns={OFFERS_COLUMNS} label="Loading offers" />}
          >
            <OffersResults query={query} />
          </Suspense>
        </CardContent>
      </Card>
    </div>
  );
}
