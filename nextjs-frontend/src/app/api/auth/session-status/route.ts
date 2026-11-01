import { cookies } from 'next/headers';
import { NextResponse } from 'next/server';
import { SESSION_COOKIE_NAME } from '@/lib/auth/session-cookie';

const NO_STORE = { 'Cache-Control': 'no-store' };

/**
 * GET /api/auth/session-status
 *
 * A cosmetic hint for the session-blind public header: `{ "signedIn": true }` when the httpOnly `session`
 * cookie is present. It does not verify the cookie (the edge and this handler cannot tell a stale cookie from a
 * good one), makes no backend call and returns no user data. The dashboard still verifies every request, so a
 * stale cookie at worst shows a "Dashboard" link that then redirects to sign-in.
 * `src/proxy.ts` lets it through unauthenticated and outside the rate limiter (it does no work).
 */
export async function GET() {
  const cookieStore = await cookies();
  const signedIn = Boolean(cookieStore.get(SESSION_COOKIE_NAME)?.value);
  return NextResponse.json({ signedIn }, { headers: NO_STORE });
}
