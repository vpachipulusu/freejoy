import Link from 'next/link';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { offerTypeLabel } from '@/lib/admin-catalogue/offer-labels';
import type { OfferRowView } from '@/lib/admin-catalogue/offer-view';
import { formatAdminDate } from '../_components/format-admin-date';
import { OfferImportBadge } from './_components/offer-import-badge';
import { OfferRowActions } from './_components/offer-row-actions';
import { OfferStatusPill } from './_components/offer-status-pill';

export type OfferTableRow = OfferRowView & { categoryName: string; merchantName: string };

export const OFFERS_COLUMNS = ['Offer', 'Category', 'Status', 'Verified', 'Actions'];

function VerifiedCell({ value }: { value: string | null }) {
  const formatted = value ? formatAdminDate(value) : '';
  if (formatted) return <span className="text-muted-foreground">{formatted}</span>;
  return <span className="text-warn-foreground">Not verified</span>;
}

/** The offers list. Below 640px each row stacks and every cell names its column. */
export function OffersTable({ rows }: { rows: OfferTableRow[] }) {
  return (
    <Table stacked aria-labelledby="offers-heading">
      <TableHeader>
        <TableRow>
          {OFFERS_COLUMNS.map((column) => (
            <TableHead key={column} className={column === 'Actions' ? 'text-right' : undefined}>
              {column}
            </TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row) => (
          <TableRow key={row.id}>
            <TableCell className="max-sm:w-full">
              <Link
                href={`/admin/offers/${row.id}`}
                className="inline-flex min-h-11 items-center break-words font-medium underline-offset-4 hover:underline"
              >
                {row.title}
              </Link>
              <div className="text-muted-foreground">
                {row.merchantName} · {offerTypeLabel(row.offerType)}
              </div>
              <OfferImportBadge
                importProvider={row.importProvider}
                sourceChangedAt={row.sourceChangedAt}
                partnerRef={row.partnerRef}
              />
            </TableCell>
            <TableCell label="Category" className="text-muted-foreground">
              {row.categoryName}
            </TableCell>
            <TableCell label="Status">
              <OfferStatusPill status={row.status} />
            </TableCell>
            <TableCell label="Verified">
              <VerifiedCell value={row.lastVerifiedAt} />
            </TableCell>
            <TableCell className="text-right">
              <div className="flex items-center justify-end gap-1">
                {row.status === 'UNKNOWN' ? null : (
                  <OfferRowActions offerId={row.id} offerTitle={row.title} status={row.status} />
                )}
              </div>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
