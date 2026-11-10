import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import type { MerchantClicksData } from '@/lib/api/admin-analytics';
import { formatCount } from '../_components/format-money';
import { formatAdminDate } from '../_components/format-admin-date';

type Offer = MerchantClicksData['offers'][number];

/**
 * The days of one offer, behind a disclosure so a 366-day range stays readable: closed it is one line, open it is
 * a short two-column table in a scrollable region (focusable, so the keyboard can scroll it).
 */
function DailyBreakdown({ offer }: { offer: Offer }) {
  if (offer.days.length === 0) return <span className="text-muted-foreground">No clicks</span>;
  const count = offer.days.length;
  return (
    <details className="w-full">
      <summary className="cursor-pointer text-sm">
        {formatCount(count)} {count === 1 ? 'day' : 'days'} with clicks
      </summary>
      <div
        role="region"
        aria-label={`Daily clicks for ${offer.title}`}
        tabIndex={0}
        className="mt-2 max-h-64 overflow-auto rounded-md border border-border"
      >
        <table className="w-full text-sm">
          <caption className="sr-only">Daily clicks for {offer.title}, in UTC days</caption>
          <thead>
            <tr className="border-b border-border text-left">
              <th scope="col" className="px-3 py-2 font-medium">
                Date (UTC)
              </th>
              <th scope="col" className="px-3 py-2 text-right font-medium">
                Clicks
              </th>
            </tr>
          </thead>
          <tbody>
            {offer.days.map((day) => (
              <tr key={day.date} className="border-b border-border last:border-0">
                <td className="px-3 py-1.5">
                  <time dateTime={day.date}>{formatAdminDate(day.date)}</time>
                </td>
                <td className="px-3 py-1.5 text-right tabular-nums">{formatCount(day.clicks)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

/** Clicks per offer with a daily breakdown for each, and the merchant's total in the footer. */
export function MerchantClicksTable({ report }: { report: MerchantClicksData }) {
  const range = `${formatAdminDate(report.from_date)} to ${formatAdminDate(report.to_date)}`;
  return (
    <Table stacked>
      <TableCaption className="sr-only">
        Clicks per offer for {report.merchant.name}, {range}, in UTC days
      </TableCaption>
      <TableHeader>
        <TableRow>
          <TableHead scope="col">Offer</TableHead>
          <TableHead scope="col" className="text-right">
            Total clicks
          </TableHead>
          <TableHead scope="col">Daily breakdown</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {report.offers.map((offer) => (
          <TableRow key={offer.offer_id}>
            <TableCell label="Offer" className="font-medium">
              {offer.title}
            </TableCell>
            <TableCell label="Total clicks" numeric className="tabular-nums">
              {formatCount(offer.total_clicks)}
            </TableCell>
            <TableCell label="Daily breakdown" className="max-sm:w-full max-sm:basis-full">
              <DailyBreakdown offer={offer} />
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
      <TableFooter>
        <TableRow>
          <TableCell label="Offer" className="font-semibold">
            {report.offers_truncated ? 'All offers of this merchant' : 'All offers'}
          </TableCell>
          <TableCell label="Total clicks" numeric className="font-semibold tabular-nums">
            {formatCount(report.total_clicks)}
          </TableCell>
          <TableCell aria-hidden="true" className="max-sm:hidden" />
        </TableRow>
      </TableFooter>
    </Table>
  );
}
