import Form from 'next/form';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  ANALYTICS_PATH,
  clicksNoticeId,
  clicksNoticeItemId,
  MAX_CLICK_RANGE_DAYS,
  type ClicksField,
  type ClicksParseResult,
} from '@/lib/admin-clicks/range';
import { NativeSelect } from '../_components/native-select';

const HINT = 'text-xs text-muted-foreground';

export type MerchantOption = { id: string; name: string };

type Parsed = Pick<ClicksParseResult, 'query' | 'notices' | 'invalid'>;

type Props = {
  parsed: Parsed;
  merchants: MerchantOption[];
  /** True when the picker lists only the first merchants of a longer list. */
  moreMerchants: boolean;
};

function fieldA11y(field: ClicksField, hintId: string | undefined, parsed: Parsed) {
  const noticeId = clicksNoticeId(field, parsed);
  return {
    'aria-invalid': noticeId ? (true as const) : undefined,
    'aria-describedby': [hintId, noticeId].filter(Boolean).join(' ') || undefined,
  };
}

function MerchantField({ parsed, merchants, moreMerchants }: Props) {
  return (
    <div className="space-y-2 sm:col-span-2 lg:col-span-1">
      <Label htmlFor="clicks-merchant">Merchant</Label>
      <NativeSelect
        key={parsed.query.merchant ?? ''}
        id="clicks-merchant"
        name="merchant"
        required
        defaultValue={parsed.query.merchant ?? ''}
        {...fieldA11y('merchant', moreMerchants ? 'clicks-merchant-hint' : undefined, parsed)}
      >
        <option value="">Choose a merchant</option>
        {merchants.map((merchant) => (
          <option key={merchant.id} value={merchant.id}>
            {merchant.name}
          </option>
        ))}
      </NativeSelect>
      {moreMerchants ? (
        <p id="clicks-merchant-hint" className={HINT}>
          Only the first {merchants.length} merchants by name are listed.
        </p>
      ) : null}
    </div>
  );
}

/** From and To. Neither has a `min` or `max`: a reversed pair is refused by the server with a notice, and a
 * limit tied to the other field would stop the admin moving the whole range in one submit. */
function DateFields({ parsed }: { parsed: Parsed }) {
  const { query } = parsed;
  return (
    <>
      <div className="space-y-2">
        <Label htmlFor="clicks-from">From date</Label>
        <Input
          key={query.from}
          id="clicks-from"
          type="date"
          name="from"
          required
          defaultValue={query.from}
          {...fieldA11y('from', 'clicks-date-hint', parsed)}
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor="clicks-to">To date</Label>
        <Input
          key={query.to}
          id="clicks-to"
          type="date"
          name="to"
          required
          defaultValue={query.to}
          {...fieldA11y('to', 'clicks-date-hint', parsed)}
        />
      </div>
    </>
  );
}

function Notices({ notices }: { notices: string[] }) {
  return (
    <div role="status" id="clicks-notices" className="mt-3">
      {notices.length > 0 ? (
        <ul className="space-y-1 text-sm text-destructive">
          {notices.map((notice) => (
            <li key={notice} id={clicksNoticeItemId(notice)}>
              {notice}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/**
 * The report's controls: a `next/form` GET form, so the merchant and the range live in the URL (shareable, and
 * Retry reruns the same report). Every control has a visible label; a rejected URL value is never put back in its
 * field, the field is marked invalid and described by the notice instead. `scroll={false}`: the section sits low on
 * the page, so the default scroll to the top after a submit would leave the report out of sight; the results
 * heading takes focus instead (`ResultsFocus`), which brings it into view.
 */
export function MerchantClicksForm({ parsed, merchants, moreMerchants }: Props) {
  return (
    <Form action={ANALYTICS_PATH} scroll={false} aria-label="Choose a merchant and a date range">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <MerchantField parsed={parsed} merchants={merchants} moreMerchants={moreMerchants} />
        <DateFields parsed={parsed} />
      </div>
      <p id="clicks-date-hint" className={`${HINT} mt-2`}>
        Dates are in UTC and both days are included. At most {MAX_CLICK_RANGE_DAYS} days.
      </p>
      <Notices notices={parsed.notices} />
      <div className="mt-4">
        <Button type="submit">Show report</Button>
      </div>
    </Form>
  );
}
