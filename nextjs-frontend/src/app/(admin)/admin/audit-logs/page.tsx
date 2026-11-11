import Link from 'next/link';
import { redirect } from 'next/navigation';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader } from '@/components/ui/card';
import { adminErrorMessage } from '@/lib/api/admin-errors';
import { fetchAdminAuditLogs } from '@/lib/api/admin-audit-logs';
import { logger } from '@/lib/logger';
import { InlineError } from '../_components/inline-error';
import { requireAdminPage } from '../_lib/require-admin-page';
import { AuditLogsFilters } from './audit-logs-filters';
import { AuditLogsPagination } from './audit-logs-pagination';
import {
  AUDIT_LOGS_PATH,
  auditLogsHref,
  auditLogsRangeText,
  entryCount,
  hasAuditLogFilters,
  isCanonicalAuditLogsUrl,
  parseAuditLogsQuery,
  toAuditLogListParams,
  type AuditLogsQuery,
} from './audit-logs-query';
import { AuditLogsTable } from './audit-logs-table';
import { ResultsFocus } from '../_components/results-focus';

type SearchParams = Promise<Record<string, string | string[] | undefined>>;

export const metadata = { title: 'Audit Logs' };

function EmptyState({ query, total }: { query: AuditLogsQuery; total: number }) {
  if (total > 0 && query.page > 1) {
    return (
      <div className="flex flex-col items-center gap-3 py-8 text-center">
        <p className="text-sm text-muted-foreground">No entries on this page</p>
        <Button variant="outline" asChild>
          <Link href={auditLogsHref({ ...query, page: 1 })}>Back to first page</Link>
        </Button>
      </div>
    );
  }
  if (!hasAuditLogFilters(query)) {
    return <p className="py-8 text-center text-sm text-muted-foreground">No audit logs found</p>;
  }
  return (
    <div className="flex flex-col items-center gap-3 py-8 text-center">
      <p className="text-sm text-muted-foreground">No entries match these filters</p>
      <Button variant="outline" asChild>
        <Link href={AUDIT_LOGS_PATH}>Reset filters</Link>
      </Button>
    </div>
  );
}

export default async function AdminAuditLogsPage({ searchParams }: { searchParams: SearchParams }) {
  await requireAdminPage();
  const rawParams = await searchParams;
  const parsed = parseAuditLogsQuery(rawParams);
  const { query, notices } = parsed;
  // A clean but untidy URL (blank fields, the default size) becomes its canonical link. With notices the URL stays
  // so the admin sees what was ignored.
  if (notices.length === 0 && !isCanonicalAuditLogsUrl(rawParams, query)) {
    redirect(auditLogsHref(query));
  }
  const result = await fetchAdminAuditLogs(toAuditLogListParams(query));
  if (!result.success) {
    logger.error('Admin audit logs page: list failed', { status: result.status });
  }
  const logs = result.success ? result.data.entries : [];
  const totalCount = result.success ? result.data.total_count : 0;
  const summary =
    logs.length > 0
      ? `${auditLogsRangeText(query, logs.length, totalCount)} (newest first)`
      : entryCount(totalCount);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Audit Logs</h1>
        <p className="text-muted-foreground">Track all administrative actions and system events</p>
      </div>

      <Card>
        <CardHeader>
          <h2 className="font-semibold leading-none tracking-tight">Filters</h2>
          <CardDescription>All filters combine: an entry must match every one.</CardDescription>
        </CardHeader>
        <CardContent>
          <AuditLogsFilters query={query} notices={notices} invalid={parsed.invalid} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <ResultsFocus focusKey={auditLogsHref(query)}>Recent Activity</ResultsFocus>
          <CardDescription role="status">
            {result.success ? summary : 'Audit log entries'}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {!result.success ? (
            <InlineError message={adminErrorMessage(result)} retryHref={auditLogsHref(query)} />
          ) : logs.length > 0 ? (
            <>
              <AuditLogsTable logs={logs} />
              <AuditLogsPagination query={query} total={totalCount} />
            </>
          ) : (
            <EmptyState query={query} total={totalCount} />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
