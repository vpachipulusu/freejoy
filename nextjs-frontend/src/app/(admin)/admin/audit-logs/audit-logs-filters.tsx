import { AnnouncingForm as Form } from '@/components/site/announcing-form';
import Link from 'next/link';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { AUDIT_LOG_PAGE_SIZES } from '@/lib/admin-audit/paging';
import { NativeSelect } from '../_components/native-select';
import {
  AUDIT_LOGS_PATH,
  auditNoticeId,
  auditNoticeItemId,
  type AuditField,
  type AuditLogsParseResult,
} from './audit-logs-query';

const HINT = 'text-xs text-muted-foreground';

type FieldProps = Pick<AuditLogsParseResult, 'query' | 'notices' | 'invalid'>;

/** The props that mark a rejected field invalid and tie it to the notice that explains why. */
function fieldA11y(field: AuditField, hintId: string | undefined, parsed: FieldProps) {
  const noticeId = auditNoticeId(field, parsed);
  return {
    'aria-invalid': noticeId ? (true as const) : undefined,
    'aria-describedby': [hintId, noticeId].filter(Boolean).join(' ') || undefined,
  };
}

/**
 * The audit log filters: a `next/form` GET form, so every filter lives in the URL (shareable, bookmarkable), the
 * navigation is soft with script and a normal GET without it. Submitting always starts again at page 1. "Reset
 * filters" is a link back to the bare path. A rejected value is not put back in its field (it is never echoed);
 * the field is marked invalid and described by the notice instead.
 */
export function AuditLogsFilters({ query, notices, invalid }: FieldProps) {
  const parsed = { query, notices, invalid };
  return (
    <Form action={AUDIT_LOGS_PATH} role="search" aria-label="Filter audit log entries">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <div className="space-y-2">
          <Label htmlFor="audit-action">Action</Label>
          <Input
            key={query.action ?? ''}
            id="audit-action"
            name="action"
            maxLength={100}
            autoComplete="off"
            spellCheck={false}
            defaultValue={query.action}
            {...fieldA11y('action', 'audit-action-hint', parsed)}
          />
          <p id="audit-action-hint" className={HINT}>
            Exact action, for example admin.offers.update
          </p>
        </div>
        <div className="space-y-2">
          <Label htmlFor="audit-user">User ID</Label>
          <Input
            key={query.user ?? ''}
            id="audit-user"
            name="user"
            maxLength={128}
            autoComplete="off"
            spellCheck={false}
            defaultValue={query.user}
            {...fieldA11y('user', 'audit-user-hint', parsed)}
          />
          <p id="audit-user-hint" className={HINT}>
            The admin who made the change
          </p>
        </div>
        <div className="space-y-2">
          <Label htmlFor="audit-org">Organisation ID</Label>
          <Input
            key={query.org ?? ''}
            id="audit-org"
            name="org"
            maxLength={128}
            autoComplete="off"
            spellCheck={false}
            defaultValue={query.org}
            {...fieldA11y('org', 'audit-org-hint', parsed)}
          />
          <p id="audit-org-hint" className={HINT}>
            The organisation the entry is about
          </p>
        </div>
        <fieldset className="grid gap-4 sm:col-span-2 sm:grid-cols-2">
          <legend className="mb-2 text-sm font-medium">Date range (UTC, whole days)</legend>
          <div className="space-y-2">
            <Label htmlFor="audit-from">From date</Label>
            <Input
              key={query.from ?? ''}
              id="audit-from"
              type="date"
              name="from"
              max={query.to}
              defaultValue={query.from}
              {...fieldA11y('from', 'audit-date-hint', parsed)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="audit-to">To date</Label>
            <Input
              key={query.to ?? ''}
              id="audit-to"
              type="date"
              name="to"
              min={query.from}
              defaultValue={query.to}
              {...fieldA11y('to', 'audit-date-hint', parsed)}
            />
          </div>
          <p id="audit-date-hint" className={`${HINT} sm:col-span-2`}>
            Dates are in UTC and include the whole day.
          </p>
        </fieldset>
        <div className="space-y-2">
          <Label htmlFor="audit-size">Entries per page</Label>
          <NativeSelect
            key={query.size}
            id="audit-size"
            name="size"
            defaultValue={String(query.size)}
          >
            {AUDIT_LOG_PAGE_SIZES.map((size) => (
              <option key={size} value={size}>
                {size}
              </option>
            ))}
          </NativeSelect>
        </div>
      </div>
      <div role="status" id="audit-filter-notices" className="mt-3">
        {notices.length > 0 ? (
          <ul className="space-y-1 text-sm text-destructive">
            {notices.map((notice) => (
              <li key={notice} id={auditNoticeItemId(notice)}>
                {notice}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
      <div className="mt-4 flex flex-wrap gap-3">
        <Button type="submit">Apply filters</Button>
        <Button variant="outline" asChild>
          <Link href={AUDIT_LOGS_PATH}>Reset filters</Link>
        </Button>
      </div>
    </Form>
  );
}
