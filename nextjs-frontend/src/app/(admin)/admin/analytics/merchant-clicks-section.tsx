import { Suspense } from 'react';
import { adminErrorMessage } from '@/lib/api/admin-errors';
import { fetchAdminMerchant, fetchAdminMerchants } from '@/lib/api/admin-merchants';
import { logCatalogueFailure } from '@/lib/api/admin-catalogue-log';
import { clicksHref, type ClicksParseResult } from '@/lib/admin-clicks/range';
import { Panel } from '@/components/dashboard/overview/panel';
import { InlineError } from '../_components/inline-error';
import { ResultsFocus } from '../_components/results-focus';
import { MerchantClicksForm, type MerchantOption } from './merchant-clicks-form';
import {
  CLICKS_REPORT_HEADING_ID,
  MerchantClicksLoading,
  MerchantClicksResults,
} from './merchant-clicks-results';
import { CLICKS_STATUS_ID } from './status-announcer';

export const CLICK_MEANING_TEXT =
  'A click is one visitor sent through a Freejoy link to the retailer’s page. It is not a sale, a ' +
  'sign-up or a claim, and it does not count unique people: someone who clicks twice counts twice. ' +
  'Clicks from visitors identified as bots are left out.';
export const RANGE_REFUSED_TEXT = 'No report was run. Pick a valid date range above.';

export type MerchantsResult = Awaited<ReturnType<typeof fetchAdminMerchants>>;

/**
 * The merchants for the picker. A merchant named in the link that the first page of the list does not hold (the
 * API lists 100 by name) is fetched by id and added, so the next submit keeps it; if that fails the picker simply
 * shows the placeholder and the report call explains.
 */
async function pickerOptions(
  list: Extract<MerchantsResult, { success: true }>,
  selected: string | undefined
): Promise<MerchantOption[]> {
  const options = list.data.merchants.map(({ id, name }) => ({ id, name }));
  if (!selected || options.some((option) => option.id === selected)) return options;
  const found = await fetchAdminMerchant(selected);
  return found.success ? [...options, { id: found.data.id, name: found.data.name }] : options;
}

/**
 * "Clicks by merchant": the one report an owner can send to a retailer who has no network report. The merchant
 * list is fetched by the page (started with its other reads, passed in as a promise); the report itself streams in
 * behind a loading line. The one status region is rendered here, outside the streamed part, and the streamed
 * pieces write into it. The page is the only caller, after `requireAdminPage`.
 */
export async function MerchantClicksSection({
  parsed,
  merchants,
}: {
  parsed: ClicksParseResult;
  merchants: Promise<MerchantsResult>;
}) {
  const list = await merchants;
  if (!list.success) logCatalogueFailure('Admin analytics page: merchant list failed', list);
  const href = clicksHref(parsed.query);
  const options = list.success ? await pickerOptions(list, parsed.query.merchant) : [];
  return (
    <Panel title="Clicks by merchant" titleId="clicks-heading" className="space-y-4">
      <p className="text-sm text-muted-foreground">{CLICK_MEANING_TEXT}</p>
      <p role="status" id={CLICKS_STATUS_ID} className="sr-only" />
      {list.success ? (
        <>
          <MerchantClicksForm
            parsed={parsed}
            merchants={options}
            moreMerchants={list.data.total_count > list.data.merchants.length}
          />
          <ResultsFocus focusKey={href} headingId={CLICKS_REPORT_HEADING_ID} level={3}>
            Report
          </ResultsFocus>
          {parsed.rangeRefused ? (
            <p className="text-sm text-muted-foreground">{RANGE_REFUSED_TEXT}</p>
          ) : (
            <Suspense key={href} fallback={<MerchantClicksLoading />}>
              <MerchantClicksResults query={parsed.query} />
            </Suspense>
          )}
        </>
      ) : (
        <InlineError message={adminErrorMessage(list)} retryHref={href} />
      )}
    </Panel>
  );
}
