import 'server-only';

import { callBackend } from './server';
import {
  adminMerchantClicks,
  adminOverview,
  adminRevenue,
  adminSystemStats,
  adminSyncSubscriptions,
} from './generated';

export type {
  MerchantClicksData,
  OverviewData,
  RevenueData,
  SyncData,
  SystemData,
} from './generated';

/**
 * Server-only calls to the platform analytics API (`/v1/admin/analytics`) for the admin analytics,
 * subscriptions and settings pages. The backend authenticates the session and checks the admin permission on
 * every call (overview: users.read, revenue: subscriptions.read, system: settings.read, sync:
 * subscriptions.sync, merchant clicks: offers.read). Revenue is in minor units of the platform currency.
 */

export function fetchAdminOverview() {
  return callBackend((client) => adminOverview({ client }));
}

export function fetchAdminRevenue() {
  return callBackend((client) => adminRevenue({ client }));
}

export function fetchAdminSystemStats() {
  return callBackend((client) => adminSystemStats({ client }));
}

/** Reads every Stripe subscription and reports drift from the stored state. Writes one audit row only. */
export function syncAdminSubscriptions() {
  return callBackend((client) => adminSyncSubscriptions({ client }));
}

export type MerchantClicksParams = { merchantId: string; from: string; to: string };

/**
 * Clicks per offer per UTC day for one merchant over a date range (both ends included, at most 366 days), for the
 * report an owner sends to a retailer. Counts only; the backend validates the range and answers 404 for an
 * unknown merchant and 422 for a bad range.
 */
export function fetchMerchantClicks({ merchantId, from, to }: MerchantClicksParams) {
  return callBackend((client) =>
    adminMerchantClicks({ client, query: { merchant_id: merchantId, from, to } })
  );
}
