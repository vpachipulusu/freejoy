/**
 * Whether this is a dedicated local e2e build serving a local host. The only place the e2e-only test header
 * `x-e2e-client-ip` may be believed (the edge limiter in `client-identifier.ts` and the trusted-header reader
 * in `lib/api/server.ts` both ask here, so they can never disagree).
 *
 * `next.config.ts` bakes `E2E_RATE_LIMIT_TEST_MODE` off in ordinary builds. The runtime restrictions also reject
 * an e2e artifact deployed to Vercel (preview and development deployments included) or reached by a public host.
 */
export const E2E_CLIENT_IP_HEADER = 'x-e2e-client-ip';

const LOCAL_HOSTNAMES = ['localhost', '127.0.0.1', '[::1]'];
const E2E_DIST_DIRS = ['.next-e2e', '.next-e2e-dev'];

/** `localhost:3000` gives `localhost`; `[::1]:3000` gives `[::1]`. */
export function hostnameOf(hostHeader: string | null | undefined): string {
  const host = (hostHeader ?? '').trim().toLowerCase();
  if (host.startsWith('[')) return host.slice(0, host.indexOf(']') + 1);
  return host.split(':')[0];
}

export function isLocalE2eBuild(hostname: string | null | undefined): boolean {
  return (
    process.env.E2E_RATE_LIMIT_TEST_MODE === '1' &&
    E2E_DIST_DIRS.includes(process.env.NEXT_DIST_DIR ?? '') &&
    !process.env.VERCEL &&
    !process.env.VERCEL_ENV &&
    LOCAL_HOSTNAMES.includes(hostname ?? '')
  );
}
