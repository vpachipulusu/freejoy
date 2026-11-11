import { StatusPill } from '@/components/ui/status-pill';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import type { PartnerKeyView } from '@/lib/admin-catalogue/partner-key-schema';
import { catalogueErrorMessage } from '@/lib/api/admin-catalogue-errors';
import { logCatalogueFailure } from '@/lib/api/admin-catalogue-log';
import { fetchAdminPartnerKeys, toPartnerKeyView } from '@/lib/api/admin-partner-keys';
import { formatAdminDateTime } from '../../_components/format-admin-date';
import { InlineError } from '../../_components/inline-error';
import { CreatePartnerKeyDialog } from './create-partner-key-dialog';
import { PARTNER_KEYS_HEADING_ID } from './partner-keys-ids';
import { RevokePartnerKeyButton } from './revoke-partner-key-button';

export const NO_WEBSITE_NOTICE = 'Add the merchant website before issuing a key.';
export const INACTIVE_NOTICE = 'Activate the merchant before issuing a key.';
export const NO_PERMISSION_NOTICE = 'You do not have permission to manage partner keys.';
export const KEYS_NOT_FOUND_MESSAGE = 'Partner keys could not be found. Refresh the page.';

type Props = {
  merchantId: string;
  merchantName: string;
  website: string | null;
  active: boolean;
};

/** Why a key cannot be issued to this merchant right now, or `null` when it can (the API enforces the same). */
export function issueBlocker(website: string | null, active: boolean): string | null {
  if (!active) return INACTIVE_NOTICE;
  if (!website) return NO_WEBSITE_NOTICE;
  return null;
}

function KeyRow({ merchantId, item }: { merchantId: string; item: PartnerKeyView }) {
  return (
    <TableRow>
      <TableCell label="Label" className="font-medium [overflow-wrap:anywhere] max-sm:w-full">
        {item.label}
      </TableCell>
      <TableCell label="Created">{formatAdminDateTime(item.createdAt)}</TableCell>
      <TableCell label="Last used">
        {item.lastUsedAt ? (
          formatAdminDateTime(item.lastUsedAt)
        ) : (
          <span className="text-muted-foreground">Never</span>
        )}
      </TableCell>
      <TableCell label="Status">
        <StatusPill variant={item.revokedAt ? 'neutral' : 'good'}>
          {item.revokedAt ? 'Revoked' : 'Active'}
        </StatusPill>
        {item.revokedAt ? (
          <span className="ms-2 text-muted-foreground">{formatAdminDateTime(item.revokedAt)}</span>
        ) : null}
      </TableCell>
      <TableCell className="text-right">
        {item.revokedAt ? null : (
          <RevokePartnerKeyButton merchantId={merchantId} keyId={item.id} label={item.label} />
        )}
      </TableCell>
    </TableRow>
  );
}

/**
 * The partner API keys of one merchant: label, created, last used, status, with "Create key" (a show-once
 * dialog) and "Revoke" (with a confirmation). The list never holds a key or its hash: the API does not return
 * them. An admin without `partner_keys.read` sees a plain note and no "Create key" button.
 */
export async function PartnerKeysSection({ merchantId, merchantName, website, active }: Props) {
  const result = await fetchAdminPartnerKeys(merchantId);
  if (!result.success) logCatalogueFailure('Admin merchant page: partner keys failed', result);
  const forbidden = !result.success && result.status === 403;
  const blocker = issueBlocker(website, active);

  return (
    <section aria-labelledby={PARTNER_KEYS_HEADING_ID} className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2
            id={PARTNER_KEYS_HEADING_ID}
            tabIndex={-1}
            className="text-xl font-semibold tracking-tight"
          >
            Partner API keys
          </h2>
          <p className="text-sm text-muted-foreground">
            Keys let {merchantName} call the partner API. A key is shown once, when it is created.
          </p>
        </div>
        {blocker || forbidden ? null : (
          <CreatePartnerKeyDialog merchantId={merchantId} merchantName={merchantName} />
        )}
      </div>
      {forbidden ? (
        <p className="text-sm text-muted-foreground">{NO_PERMISSION_NOTICE}</p>
      ) : blocker ? (
        <p className="text-sm text-muted-foreground">{blocker}</p>
      ) : null}
      {result.success ? (
        result.data.length === 0 ? (
          <p className="text-sm text-muted-foreground">No keys yet.</p>
        ) : (
          <Table stacked aria-labelledby={PARTNER_KEYS_HEADING_ID}>
            <TableHeader>
              <TableRow>
                <TableHead>Label</TableHead>
                <TableHead>Created</TableHead>
                <TableHead>Last used</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>
                  <span className="sr-only">Actions</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {result.data.map((key) => (
                <KeyRow key={key.id} merchantId={merchantId} item={toPartnerKeyView(key)} />
              ))}
            </TableBody>
          </Table>
        )
      ) : forbidden ? null : (
        <InlineError
          message={catalogueErrorMessage(result, KEYS_NOT_FOUND_MESSAGE)}
          retryHref={`/admin/merchants/${merchantId}`}
        />
      )}
    </section>
  );
}
