import { Suspense } from 'react';
import { adminErrorMessage } from '@/lib/api/admin-errors';
import { fetchAdminMerchants } from '@/lib/api/admin-merchants';
import { logCatalogueFailure } from '@/lib/api/admin-catalogue-log';
import { clicksHref, type ClicksParseResult } from '@/lib/admin-clicks/range';
import { InlineError } from '../_components/inline-error';
import { ResultsFocus } from '../audit-logs/results-focus';
import { MerchantClicksForm } from './merchant-clicks-form';
import { MerchantClicksLoading, MerchantClicksResults } from './merchant-clicks-results';

export const CLICK_MEANING_TEXT =
  'A click is one visitor sent through a Freejoy link to the retailer’s page. It is not a sale, a ' +
  'sign-up or a claim, and it does not count unique people: someone who clicks twice counts twice. ' +
  'Clicks from visitors identified as bots are left out.';

/**
 * "Clicks by merchant": the one report an owner can send to a retailer who has no network report. The merchant
 * list comes from the admin merchants API (offers.read); the report itself streams in behind a loading message, so
 * the form is usable at once. The page is the only caller, after `requireAdminPage`.
 */
export async function MerchantClicksSection({ parsed }: { parsed: ClicksParseResult }) {
  const merchants = await fetchAdminMerchants();
  if (!merchants.success) {
    logCatalogueFailure('Admin analytics page: merchant list failed', merchants);
  }
  const href = clicksHref(parsed.query);
  return (
    <section
      aria-labelledby="clicks-heading"
      className="space-y-4 rounded-xl border border-border bg-card p-4"
    >
      <div className="space-y-1">
        <ResultsFocus focusKey={href} headingId="clicks-heading">
          Clicks by merchant
        </ResultsFocus>
        <p className="text-sm text-muted-foreground">{CLICK_MEANING_TEXT}</p>
      </div>
      {merchants.success ? (
        <>
          <MerchantClicksForm
            parsed={parsed}
            merchants={merchants.data.merchants.map(({ id, name }) => ({ id, name }))}
            moreMerchants={merchants.data.total_count > merchants.data.merchants.length}
          />
          <Suspense key={href} fallback={<MerchantClicksLoading />}>
            <MerchantClicksResults query={parsed.query} />
          </Suspense>
        </>
      ) : (
        <InlineError message={adminErrorMessage(merchants)} retryHref={href} />
      )}
    </section>
  );
}
