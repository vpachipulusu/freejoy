import 'server-only';

import type { PartnerKeyView } from '@/lib/admin-catalogue/partner-key-schema';
import { callBackend } from './server';
import {
  adminCreatePartnerKey,
  adminListPartnerKeys,
  adminRevokePartnerKey,
  type PartnerKeyOut,
} from './generated';

/**
 * Server-only calls to the admin partner keys API (`/v1/admin/merchants/{id}/partner-keys`). Listing needs
 * `partner_keys.read`, issuing and revoking `partner_keys.write`; the backend checks both and writes the audit log.
 * The key value comes back from `adminCreatePartnerKey` once; nothing here stores or logs it.
 */

export function fetchAdminPartnerKeys(merchantId: string) {
  return callBackend((client) =>
    adminListPartnerKeys({ client, path: { merchant_id: merchantId } })
  );
}

export function createAdminPartnerKey(merchantId: string, label: string) {
  return callBackend((client) =>
    adminCreatePartnerKey({ client, path: { merchant_id: merchantId }, body: { label } })
  );
}

export function revokeAdminPartnerKey(merchantId: string, keyId: string) {
  return callBackend((client) =>
    adminRevokePartnerKey({ client, path: { merchant_id: merchantId, key_id: keyId } })
  );
}

export function toPartnerKeyView(key: PartnerKeyOut): PartnerKeyView {
  return {
    id: key.id,
    label: key.label,
    createdAt: key.created_at,
    lastUsedAt: key.last_used_at ?? null,
    revokedAt: key.revoked_at ?? null,
  };
}
