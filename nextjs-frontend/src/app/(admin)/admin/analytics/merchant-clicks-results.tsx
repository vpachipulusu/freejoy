import { adminErrorMessage } from '@/lib/api/admin-errors';
import { fetchMerchantClicks } from '@/lib/api/admin-analytics';
import { logCatalogueFailure } from '@/lib/api/admin-catalogue-log';
import { clicksHref, daysInclusive, type ClicksQuery } from '@/lib/admin-clicks/range';
import { InlineError } from '../_components/inline-error';
import { formatCount } from '../_components/format-money';
import { formatAdminDate } from '../_components/format-admin-date';
import { DownloadClicksButton } from './download-clicks-button';
import { MerchantClicksTable } from './merchant-clicks-table';

export const CHOOSE_MERCHANT_TEXT = 'Choose a merchant and a date range to see its clicks.';
export const MERCHANT_GONE_MESSAGE = 'That merchant no longer exists. Choose another.';
export const NO_OFFERS_TEXT = 'This merchant has no offers to report on yet.';

const plural = (count: number, one: string, many: string) =>
  `${formatCount(count)} ${count === 1 ? one : many}`;

/** The report for the merchant and range in the URL: error, empty and result states. */
export async function MerchantClicksResults({ query }: { query: ClicksQuery }) {
  if (!query.merchant) {
    return <p className="text-sm text-muted-foreground">{CHOOSE_MERCHANT_TEXT}</p>;
  }
  const result = await fetchMerchantClicks({
    merchantId: query.merchant,
    from: query.from,
    to: query.to,
  });
  if (!result.success) {
    logCatalogueFailure('Admin analytics page: merchant clicks failed', result);
    return (
      <InlineError
        message={adminErrorMessage(result, MERCHANT_GONE_MESSAGE)}
        retryHref={clicksHref(query)}
      />
    );
  }

  const report = result.data;
  if (report.offers.length === 0) {
    return <p className="text-sm text-muted-foreground">{NO_OFFERS_TEXT}</p>;
  }
  const days = daysInclusive(report.from_date, report.to_date);
  const summary =
    `${report.merchant.name}: ${plural(report.total_clicks, 'click', 'clicks')} from ` +
    `${formatAdminDate(report.from_date)} to ${formatAdminDate(report.to_date)} ` +
    `(${plural(days, 'day', 'days')}, UTC).`;

  return (
    <div className="space-y-4">
      <p role="status" className="text-sm font-medium">
        {summary}
        {report.total_clicks === 0 ? ' No clicks were recorded in this range.' : ''}
      </p>
      {report.retention.starts_before_retention ? (
        <p className="rounded-md border border-border p-3 text-sm">
          Click records are kept for {report.retention.days} days, so this range starts before the
          data does. Clicks before {formatAdminDate(report.retention.earliest_complete_date)} are
          missing and the counts for those days are too low.
        </p>
      ) : null}
      {report.offers_truncated ? (
        <p className="rounded-md border border-border p-3 text-sm">
          Only the {formatCount(report.max_offers)} offers with the most clicks are listed, out of{' '}
          {formatCount(report.offers_total)}. The total at the bottom and the CSV file follow the
          same limit: the total counts every offer, the file lists only those shown.
        </p>
      ) : null}
      <MerchantClicksTable report={report} />
      {report.excluded_bot_clicks > 0 ? (
        <p className="text-sm text-muted-foreground">
          {plural(report.excluded_bot_clicks, 'click', 'clicks')} from automated visitors (bots)
          were left out of these counts.
        </p>
      ) : null}
      <DownloadClicksButton merchantId={query.merchant} from={query.from} to={query.to} />
    </div>
  );
}

/** What the visitor sees while the report loads (the Suspense fallback). */
export function MerchantClicksLoading() {
  return (
    <p role="status" className="text-sm text-muted-foreground">
      Loading the click report…
    </p>
  );
}
