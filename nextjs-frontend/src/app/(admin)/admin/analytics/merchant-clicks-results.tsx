import { fetchMerchantClicks, type MerchantClicksData } from '@/lib/api/admin-analytics';
import {
  catalogueErrorMessage,
  CLICKS_MERCHANT_NOT_FOUND_MESSAGE,
} from '@/lib/api/admin-catalogue-errors';
import { logCatalogueFailure } from '@/lib/api/admin-catalogue-log';
import { daysInclusive, type ClicksQuery } from '@/lib/admin-clicks/range';
import { InlineError } from '../_components/inline-error';
import { formatCount } from '../_components/format-money';
import { formatAdminDate } from '../_components/format-admin-date';
import { DownloadClicksButton } from './download-clicks-button';
import { MerchantClicksTable } from './merchant-clicks-table';
import { RetryClicksButton } from './retry-clicks-button';
import { StatusAnnouncer } from './status-announcer';

export const CHOOSE_MERCHANT_TEXT = 'Choose a merchant and a date range to see its clicks.';
export const NO_OFFERS_TEXT = 'This merchant has no offers to report on yet.';
/** The id of the results heading: where Retry sends keyboard focus. */
export const CLICKS_REPORT_HEADING_ID = 'clicks-report-heading';

const NOTE = 'rounded-md border border-border p-3 text-sm';

const plural = (count: number, one: string, many: string) =>
  `${formatCount(count)} ${count === 1 ? one : many}`;

function summaryOf(report: MerchantClicksData): string {
  const days = daysInclusive(report.from_date, report.to_date);
  return (
    `${report.merchant.name}: ${plural(report.total_clicks, 'click', 'clicks')} from ` +
    `${formatAdminDate(report.from_date)} to ${formatAdminDate(report.to_date)} ` +
    `(${plural(days, 'day', 'days')}, UTC).` +
    (report.total_clicks === 0 ? ' No clicks were recorded in this range.' : '')
  );
}

function Notes({ report }: { report: MerchantClicksData }) {
  return (
    <>
      {report.retention.starts_before_retention ? (
        <p className={NOTE}>
          Click records are kept for {report.retention.days} days, so this range starts before the
          data does. Clicks before {formatAdminDate(report.retention.earliest_complete_date)} are
          missing and the counts for those days are too low.
        </p>
      ) : null}
      {report.offers_truncated ? (
        <p className={NOTE}>
          Only the {formatCount(report.max_offers)} offers with the most clicks are listed, out of{' '}
          {formatCount(report.offers_total)}. The total above and at the bottom counts every offer
          of this merchant. The table and the CSV file list only these{' '}
          {formatCount(report.max_offers)}, so the file adds up to less.
        </p>
      ) : null}
    </>
  );
}

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
        message={catalogueErrorMessage(result, CLICKS_MERCHANT_NOT_FOUND_MESSAGE)}
        retry={<RetryClicksButton focusId={CLICKS_REPORT_HEADING_ID} />}
      />
    );
  }

  const report = result.data;
  if (report.offers.length === 0) {
    return <p className="text-sm text-muted-foreground">{NO_OFFERS_TEXT}</p>;
  }
  const summary = summaryOf(report);

  return (
    <div className="space-y-4">
      <p className="text-sm font-medium">{summary}</p>
      <StatusAnnouncer message={summary} />
      <Notes report={report} />
      <MerchantClicksTable report={report} />
      {report.excluded_bot_clicks > 0 ? (
        <p className="text-sm text-muted-foreground">
          {plural(report.excluded_bot_clicks, 'click', 'clicks')} from automated visitors (bots)
          were left out of these counts. This is a minimum: recording of bot clicks is capped, so
          there may have been more.
        </p>
      ) : null}
      <DownloadClicksButton merchantId={query.merchant} from={query.from} to={query.to} />
    </div>
  );
}

/** What the visitor sees while the report loads (the Suspense fallback); the same text goes to the status region. */
export function MerchantClicksLoading() {
  return (
    <>
      <p className="text-sm text-muted-foreground">Loading the click report…</p>
      <StatusAnnouncer message="Loading the click report…" />
    </>
  );
}
