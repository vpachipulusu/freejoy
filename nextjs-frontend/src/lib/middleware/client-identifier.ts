import { isIP } from 'node:net';
import type { NextRequest } from 'next/server';
import { isLocalE2eBuild } from './local-e2e';

function validIp(value: string | null): string | null {
  const ip = value?.trim();
  return ip && isIP(ip) !== 0 ? ip : null;
}

/** Select an identity only across a trusted platform boundary, never from raw forwarded headers. */
export function getClientIdentifier(request: NextRequest): string {
  if (process.env.VERCEL === '1') {
    // Vercel supplies this header independently of caller-supplied X-Forwarded-For.
    // https://vercel.com/docs/headers/request-headers#x-vercel-forwarded-for
    return validIp(request.headers.get('x-vercel-forwarded-for')) ?? 'unknown';
  }

  // The same test as the trusted-header reader in lib/api/server.ts (see local-e2e.ts).
  if (isLocalE2eBuild(request.nextUrl.hostname)) {
    const testIp = validIp(request.headers.get('x-e2e-client-ip'));
    if (testIp) return `e2e:${testIp}`;
  }

  // Outside Vercel there is no established trusted proxy; share a conservative bucket.
  return 'unknown';
}
