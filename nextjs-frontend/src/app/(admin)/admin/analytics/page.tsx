import { adminErrorMessage } from '@/lib/api/admin-errors';
import {
  fetchAdminOverview,
  fetchAdminRevenue,
  type OverviewData,
  type RevenueData,
} from '@/lib/api/admin-analytics';
import type { ApiResult } from '@/lib/api/server';
import { logger } from '@/lib/logger';
import { KpiStrip, type KpiCell } from '@/components/dashboard/kpi-strip';
import { Panel } from '@/components/dashboard/overview/panel';
import { RefreshButton } from '@/components/admin/refresh-button';
import { fetchAdminMerchants } from '@/lib/api/admin-merchants';
import { clicksHref, parseClicksQuery } from '@/lib/admin-clicks/range';
import { InlineError } from '../_components/inline-error';
import { formatCount, formatMinorUnits } from '../_components/format-money';
import { requireAdminPage } from '../_lib/require-admin-page';
import { MerchantClicksSection } from './merchant-clicks-section';

type SearchParams = Promise<Record<string, string | string[] | undefined>>;

// Always show current figures.
export const dynamic = 'force-dynamic';
export const revalidate = 0;

const number = formatCount;

function failed(what: string, result: Extract<ApiResult<unknown>, { success: false }>) {
  logger.error(`Admin analytics page: ${what} failed`, { status: result.status });
  return adminErrorMessage(result);
}

function countRows(counts: Record<string, number>) {
  const entries = Object.entries(counts).sort(([a], [b]) => a.localeCompare(b));
  return entries.length === 0 ? (
    <p className="text-sm text-muted-foreground">None yet</p>
  ) : (
    <dl className="grid grid-cols-[1fr_auto] gap-x-6 gap-y-1 text-sm">
      {entries.map(([name, count]) => (
        <div key={name} className="contents">
          <dt className="capitalize text-muted-foreground">
            {name.replaceAll('_', ' ').toLowerCase()}
          </dt>
          <dd className="text-right tabular-nums">{number(count)}</dd>
        </div>
      ))}
    </dl>
  );
}

function overviewCells({ users, organizations }: OverviewData): KpiCell[] {
  return [
    { id: 'users', label: 'Users', state: 'ready', value: number(users.total) },
    {
      id: 'new-users',
      label: 'New this month',
      state: 'ready',
      value: number(users.new_this_month),
    },
    {
      id: 'active',
      label: 'Active, 30 days',
      state: 'ready',
      value: number(users.active_last_30_days),
    },
    { id: 'orgs', label: 'Organisations', state: 'ready', value: number(organizations.total) },
  ];
}

function revenueCells(revenue: RevenueData): KpiCell[] {
  const money = (minor: number) => formatMinorUnits(minor, revenue.currency);
  return [
    { id: 'mrr', label: 'MRR', state: 'ready', value: money(revenue.mrr_minor) },
    { id: 'arr', label: 'ARR', state: 'ready', value: money(revenue.arr_minor) },
    {
      id: 'billed',
      label: 'Paying organisations',
      state: 'ready',
      value: number(revenue.billed_organizations),
    },
    {
      id: 'cancelling',
      label: 'Cancelling',
      state: 'ready',
      value: number(revenue.cancelling_at_period_end),
    },
  ];
}

export default async function AdminAnalyticsPage({ searchParams }: { searchParams: SearchParams }) {
  await requireAdminPage();
  const parsedClicks = parseClicksQuery(await searchParams, new Date());
  // The merchant list for the clicks section starts with the other reads; the section awaits it.
  const merchants = fetchAdminMerchants();
  const [overview, revenue] = await Promise.all([fetchAdminOverview(), fetchAdminRevenue()]);
  // Retry keeps the report the admin was looking at: the same merchant and range.
  const retryHref = clicksHref(parsedClicks.query);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Analytics</h1>
          <p className="text-muted-foreground">
            Platform users, organisations and revenue, and clicks by merchant
          </p>
        </div>
        <RefreshButton />
      </div>

      {overview.success ? (
        <>
          <KpiStrip cells={overviewCells(overview.data)} />
          <div className="grid gap-4 md:grid-cols-2">
            <Panel title="User summary" titleId="admin-users-summary">
              {countRows({
                verified: overview.data.users.verified,
                admins: overview.data.users.admins,
                'new organisations this month': overview.data.organizations.new_this_month,
              })}
            </Panel>
          </div>
        </>
      ) : (
        <InlineError message={failed('overview', overview)} retryHref={retryHref} />
      )}

      {revenue.success ? (
        <>
          <KpiStrip cells={revenueCells(revenue.data)} />
          {!revenue.data.complete ? (
            <p role="status" className="text-sm text-muted-foreground">
              {revenue.data.unpriced_organizations} paying organisation(s) could not be priced, so
              revenue is a minimum.
            </p>
          ) : null}
          <div className="grid gap-4 md:grid-cols-2">
            <Panel title="Revenue by plan" titleId="admin-revenue-by-plan">
              {revenue.data.by_plan.length === 0 ? (
                <p className="text-sm text-muted-foreground">No paying organisations yet</p>
              ) : (
                <dl className="grid grid-cols-[1fr_auto] gap-x-6 gap-y-1 text-sm">
                  {revenue.data.by_plan.map((item) => (
                    <div key={item.plan} className="contents">
                      <dt className="capitalize text-muted-foreground">
                        {item.plan} ({number(item.organizations)})
                      </dt>
                      <dd className="text-right tabular-nums">
                        {formatMinorUnits(item.mrr_minor, revenue.data.currency)} / month
                      </dd>
                    </div>
                  ))}
                </dl>
              )}
            </Panel>
            <Panel title="Organisations by status" titleId="admin-orgs-by-status">
              {countRows(revenue.data.organizations_by_status)}
            </Panel>
          </div>
        </>
      ) : (
        <InlineError message={failed('revenue', revenue)} retryHref={retryHref} />
      )}

      <MerchantClicksSection parsed={parsedClicks} merchants={merchants} />
    </div>
  );
}
