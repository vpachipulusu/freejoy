import type { NextRequest } from 'next/server';
import { NextResponse } from 'next/server';
import { handleOutboundRequest } from '@/lib/outbound/respond';

/**
 * `/go/[offerId]`: the first-party outbound redirect for affiliate offers (not under `/api`).
 *
 * Only the path id is used, plus the exact query value `nd=1` which can only make the backend stricter
 * (see `@/lib/outbound/respond`); every other query parameter is ignored. The
 * destination is resolved by the backend from the stored affiliate link, so nothing the visitor sends
 * can choose where they are redirected. The response sets no cookie.
 */

export const dynamic = 'force-dynamic';

export async function GET(request: NextRequest, context: { params: Promise<{ offerId: string }> }) {
  const { offerId } = await context.params;
  return handleOutboundRequest(request, offerId);
}

/** A HEAD request (link checkers, previews) must never count as a click or reveal a destination. */
export function HEAD() {
  return new NextResponse(null, {
    status: 405,
    headers: { Allow: 'GET', 'Cache-Control': 'no-store' },
  });
}
