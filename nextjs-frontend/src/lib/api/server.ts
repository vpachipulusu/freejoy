import 'server-only';

import { isIP } from 'node:net';
import { cookies, headers } from 'next/headers';
import { BACKEND_NOT_CONFIGURED_CODE } from '@/lib/auth/auth-error-messages';
import { SESSION_COOKIE_NAME } from '@/lib/auth/session-cookie';
import { E2E_CLIENT_IP_HEADER, hostnameOf, isLocalE2eBuild } from '@/lib/middleware/local-e2e';
import { createClient, createConfig, type Client } from './generated/client';

/**
 * Server-only entry point to the FastAPI backend (ADR-003, ADR-008, ADR-011).
 *
 * Call generated SDK functions through `callBackend`, which builds the client from
 * BACKEND_API_URL, forwards the Firebase `session` cookie to the backend in the
 * `X-Session-Token` header, and folds every outcome into the `{ success, ... }` envelope.
 * The browser never calls the backend, and nothing here reaches a client bundle.
 *
 * BACKEND_INTERNAL_KEY is server-only and forwarded as X-Internal-Key (ADR-003).
 * Public health endpoints remain callable without it; auth endpoints reject missing keys.
 */

export { SESSION_COOKIE_NAME };
export const SESSION_HEADER = 'X-Session-Token';
export const INTERNAL_KEY_HEADER = 'X-Internal-Key';

export const BACKEND_UNAVAILABLE_MESSAGE = 'Backend unavailable';
export const BACKEND_ERROR_MESSAGE = 'Request failed';

/**
 * `status` is the backend HTTP status when the backend answered (absent when it was unreachable). `code` is the
 * stable machine-readable code some routes add to their error body (the admin catalogue's `slug_taken`, ...);
 * branch on it, never on `error`.
 */
export type ApiResult<T> =
  | { success: true; data: T }
  | { success: false; error: string; status?: number; code?: string };

type SdkResult<TData> = { data?: TData; error?: unknown; response?: Response };

/** Generated success envelopes are unwrapped; plain success bodies retain their type. */
export type BackendData<T> = T extends { data: infer TPayload; success?: true } ? TPayload : T;

/** A missing backend setting. Sign-in maps it to a service-unavailable result; the text is for server logs only. */
function notConfigured(variable: string): Error {
  return Object.assign(new Error(`${variable} is not set`), { code: BACKEND_NOT_CONFIGURED_CODE });
}

function getBackendUrl(): string {
  const url = process.env.BACKEND_API_URL;
  if (!url) {
    throw notConfigured('BACKEND_API_URL');
  }
  return url.replace(/\/+$/, '');
}

/** `withSession: false` leaves the caller's session cookie out, for public internal-key-only routes. */
export interface BackendOptions {
  withSession?: boolean;
  /** Throw when BACKEND_INTERNAL_KEY is missing; for routes the backend rejects without it (sign-in). */
  requireInternalKey?: boolean;
  /**
   * For reads shared by every visitor inside `unstable_cache` (a public catalogue): no session, no
   * request headers read (the caller supplies `clientIp` instead), and no `cache: 'no-store'`, which would
   * make a cached page dynamic. Never use it for anything that depends on who is asking.
   */
  shared?: boolean;
  /**
   * With `shared`, the visitor address to forward as `X-Client-IP`. `shared` reads no request headers, so the
   * caller reads it before the cache boundary with `readTrustedClientIp()` and passes it through. Never pass
   * an address from a header the client can set.
   */
  clientIp?: string;
}

const HEADER_NAME = /^[a-z0-9-]+$/i;

/**
 * Headers that can never be the configured trusted one: they are client-settable or chained lists
 * (x-forwarded-for, forwarded, via), or already handled with its own platform check
 * (x-vercel-forwarded-for, which is used automatically when VERCEL=1).
 */
const NEVER_TRUSTED = new Set(['x-forwarded-for', 'forwarded', 'via', 'x-vercel-forwarded-for']);
const warnedNeverTrusted = new Set<string>();

/** A single plain address: no IPv6 zone ("%eth0") and no brackets, which a backend must not receive. */
function isPlainIp(value: string): boolean {
  return isIP(value) !== 0 && !value.includes('%') && !value.includes('[');
}

async function warnNeverTrusted(name: string): Promise<void> {
  if (warnedNeverTrusted.has(name)) return;
  warnedNeverTrusted.add(name);
  const { logger } = await import('@/lib/logger');
  logger.warn('TRUSTED_CLIENT_IP_HEADER names a header that cannot be trusted; it is ignored', {
    header: name,
  });
}

/** Whether this request is to a local e2e build, the only place `x-e2e-client-ip` may be believed. */
async function isLocalE2eRequest(): Promise<boolean> {
  try {
    return isLocalE2eBuild(hostnameOf((await headers()).get('host')));
  } catch {
    return false; // no request scope
  }
}

/** The headers whose value may be taken as the visitor's address, in order of preference. */
async function trustedClientIpHeaders(): Promise<string[]> {
  const names: string[] = [];
  if (process.env.VERCEL === '1') names.push('x-vercel-forwarded-for');
  // One header the operator vouches for: only set it when the reverse proxy overwrites it on every request.
  const configured = process.env.TRUSTED_CLIENT_IP_HEADER?.trim().toLowerCase();
  if (configured && HEADER_NAME.test(configured)) {
    // The e2e test header is client-settable like any other: believed only in a local e2e build.
    const refused =
      NEVER_TRUSTED.has(configured) ||
      (configured === E2E_CLIENT_IP_HEADER && !(await isLocalE2eRequest()));
    if (refused) await warnNeverTrusted(configured);
    else names.push(configured);
  }
  return names;
}

/**
 * The visitor's address from a trusted header, or undefined (none configured, header absent or not an IP).
 * Trusted: Vercel's `x-vercel-forwarded-for` (when VERCEL=1) and the single header named by the server-only
 * TRUSTED_CLIENT_IP_HEADER. Never X-Forwarded-For or any other header a client can set. Needs a request
 * scope; callers outside one must catch the error.
 */
export async function readTrustedClientIp(): Promise<string | undefined> {
  const names = await trustedClientIpHeaders();
  if (names.length === 0) return undefined;
  const requestHeaders = await headers();
  for (const name of names) {
    const value = requestHeaders.get(name)?.trim();
    if (value && isPlainIp(value)) return value;
  }
  return undefined;
}

/** A fresh client per call, so one request's session can never leak into another. */
export async function getBackendClient({
  withSession = true,
  requireInternalKey = false,
  shared = false,
  clientIp,
}: BackendOptions = {}): Promise<Client> {
  const sessionToken =
    withSession && !shared ? (await cookies()).get(SESSION_COOKIE_NAME)?.value : undefined;
  const internalKey = process.env.BACKEND_INTERNAL_KEY;
  if (requireInternalKey && !internalKey) {
    throw notConfigured('BACKEND_INTERNAL_KEY');
  }
  // Caller headers are trusted only at Vercel's boundary, never raw X-Forwarded-For.
  let forwardedIp: string | undefined;
  if (internalKey) forwardedIp = shared ? clientIp : await readTrustedClientIp();
  return createClient(
    createConfig({
      baseUrl: getBackendUrl(),
      ...(shared ? {} : { cache: 'no-store' as const }),
      headers: {
        ...(sessionToken ? { [SESSION_HEADER]: sessionToken } : {}),
        ...(internalKey ? { [INTERNAL_KEY_HEADER]: internalKey } : {}),
        ...(forwardedIp && isPlainIp(forwardedIp) ? { 'X-Client-IP': forwardedIp } : {}),
      },
    })
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

/** The backend's error envelope is `{ success: false, error: string }`. */
function errorMessage(error: unknown): string {
  if (isRecord(error) && error.success === false && typeof error.error === 'string') {
    return error.error;
  }
  return BACKEND_ERROR_MESSAGE;
}

/** The stable `code` of an error body, when it is a string. */
function errorCode(error: unknown): string | undefined {
  return isRecord(error) && typeof error.code === 'string' ? error.code : undefined;
}

/** Unwrap `{ success: true, data }` bodies; pass other success bodies through. */
function unwrap<T>(body: unknown): BackendData<T> {
  if (isRecord(body) && body.success === true && 'data' in body) {
    return body.data as BackendData<T>;
  }
  return body as BackendData<T>;
}

export async function callBackend<TData>(
  call: (client: Client) => Promise<SdkResult<TData>>,
  options: BackendOptions = {}
): Promise<ApiResult<BackendData<TData>>> {
  const client = await getBackendClient(options);
  let result: SdkResult<TData>;
  try {
    result = await call(client);
  } catch {
    return { success: false, error: BACKEND_UNAVAILABLE_MESSAGE };
  }

  // The generated client reports a failed fetch (no response) through `error`, not by throwing.
  if (result.error !== undefined && !result.response) {
    return { success: false, error: BACKEND_UNAVAILABLE_MESSAGE };
  }
  if (result.error !== undefined || (result.response && !result.response.ok)) {
    const code = errorCode(result.error);
    return {
      success: false,
      error: errorMessage(result.error),
      status: result.response?.status,
      ...(code ? { code } : {}),
    };
  }
  return { success: true, data: unwrap<TData>(result.data) };
}
