/**
 * Next.js Proxy (formerly Middleware)
 *
 * Handles authentication, rate limiting, and request processing
 * Note: Migrated from middleware.ts to proxy.ts for Next.js 16+
 */

import { NextRequest, NextResponse } from 'next/server';
import { getClientIdentifier } from '@/lib/middleware/client-identifier';
import {
  rateLimit,
  getRateLimitHeaders,
  RATE_LIMIT_CONFIGS,
  RateLimitConfig,
} from '@/lib/middleware/rate-limit';

/** The first-party affiliate redirect. Rate limited with the general policy; never part of the /api allowlist. */
const OUTBOUND_PREFIX = '/go/';

/**
 * Cosmetic session hint polled by the public header on every full page load. It reads one cookie and does no
 * I/O, so it is unauthenticated (signed-out visitors must get `{"signedIn":false}`, not 401) and outside the rate
 * limiter: under `/api/auth/` it would otherwise spend the 5-per-15-minutes sign-in bucket, and off Vercel every
 * visitor shares one bucket. Exact match only.
 */
const SESSION_STATUS_PATH = '/api/auth/session-status';

/** Security headers shared by every response the proxy lets through. */
function applySecurityHeaders(response: NextResponse): void {
  response.headers.set('X-Content-Type-Options', 'nosniff');
  response.headers.set('X-Frame-Options', 'DENY');
  response.headers.set('X-XSS-Protection', '1; mode=block');
  response.headers.set('Referrer-Policy', 'strict-origin-when-cross-origin');
}

/**
 * Apply rate limiting to request
 */
function applyRateLimit(request: NextRequest, pathname: string): NextResponse | null {
  const identifier = getClientIdentifier(request);

  let rateLimitConfig: RateLimitConfig = RATE_LIMIT_CONFIGS.general;

  // Apply different rate limits based on path
  if (pathname.startsWith('/api/auth/')) {
    rateLimitConfig = RATE_LIMIT_CONFIGS.auth;
  } else if (pathname.startsWith('/api/')) {
    rateLimitConfig = RATE_LIMIT_CONFIGS.api;
  }

  const result = rateLimit(identifier, rateLimitConfig);

  if (!result.success) {
    const refused = NextResponse.json(
      { error: result.message || 'Too many requests' },
      {
        status: 429,
        headers: { ...getRateLimitHeaders(result), 'Cache-Control': 'no-store' },
      }
    );
    applySecurityHeaders(refused);
    return refused;
  }

  return null;
}

/**
 * Main proxy function (Next.js 16+ convention)
 * Note: Function must be named "proxy" for Next.js 16+
 */
export async function proxy(request: NextRequest) {
  const pathname = request.nextUrl.pathname;

  // Apply rate limiting to API routes and to the outbound redirect (`/go/`, general policy)
  if (
    (pathname.startsWith('/api/') && pathname !== SESSION_STATUS_PATH) ||
    pathname.startsWith(OUTBOUND_PREFIX)
  ) {
    const rateLimitResponse = applyRateLimit(request, pathname);
    if (rateLimitResponse) {
      return rateLimitResponse;
    }
  }

  // Auth pages that should redirect if user is already authenticated
  const authPages = ['/login', '/signup'];
  const isAuthPage = authPages.some((route) => pathname.startsWith(route));
  const authToken = request.cookies.get('session');

  if (isAuthPage && authToken) {
    // Redirect authenticated users away from auth pages to dashboard
    const dashboardUrl = new URL('/dashboard', request.url);
    return NextResponse.redirect(dashboardUrl);
  }

  // Protected routes that require authentication
  const protectedRoutes = ['/dashboard', '/settings', '/profile', '/security'];
  const isProtectedRoute = protectedRoutes.some((route) => pathname.startsWith(route));

  if (isProtectedRoute) {
    // Check for authentication cookie or session
    if (!authToken) {
      // Redirect to login if not authenticated
      const loginUrl = new URL('/login', request.url);
      loginUrl.searchParams.set('redirect', pathname);
      return NextResponse.redirect(loginUrl);
    }

    // Email verification is NOT decided here. The edge cannot verify Firebase tokens, and the
    // `email-verified` cookie is client-writable, so it must never gate access. The signed
    // `email_verified` claim is enforced server-side by `@/lib/auth/verified-session`, which the
    // dashboard/admin layouts, server actions and admin helpers call.
  }

  // Admin routes
  const isAdminRoute = pathname.startsWith('/admin');

  if (isAdminRoute) {
    const authToken = request.cookies.get('session');

    if (!authToken) {
      const loginUrl = new URL('/login', request.url);
      loginUrl.searchParams.set('redirect', pathname);
      return NextResponse.redirect(loginUrl);
    }

    // Admin role verification would be done in the API route or page
    // as we can't easily verify Firebase tokens in edge middleware
  }

  // Public API routes (no auth required)
  const publicApiExact = ['/api/health', SESSION_STATUS_PATH];
  const isPublicApiRoute = publicApiExact.includes(pathname);

  // Protected API routes (require authentication)
  if (pathname.startsWith('/api/') && !isPublicApiRoute) {
    const authHeader = request.headers.get('authorization');

    if (!authHeader || !authHeader.startsWith('Bearer ')) {
      // Check for cookie-based auth as fallback
      const authToken = request.cookies.get('session');

      if (!authToken) {
        return NextResponse.json(
          { error: 'Unauthorized: Authentication required' },
          { status: 401 }
        );
      }
    }
  }

  // Add security headers
  const response = NextResponse.next();
  applySecurityHeaders(response);
  // Alerts confirm and unsubscribe links carry a token in the query string: never send it as a Referer.
  if (pathname === '/alerts/confirm' || pathname.startsWith('/alerts/unsubscribe')) {
    response.headers.set('Referrer-Policy', 'no-referrer');
  }

  // CORS headers for API routes
  if (pathname.startsWith('/api/')) {
    response.headers.set('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS');
    response.headers.set('Access-Control-Allow-Headers', 'Content-Type, Authorization');
    response.headers.set('Access-Control-Max-Age', '86400');
  }

  return response;
}

/**
 * Proxy configuration
 * Defines which routes this proxy should handle
 */
export const config = {
  matcher: [
    /*
     * Match all request paths except:
     * - _next/static (static files)
     * - _next/image (image optimization files)
     * - favicon.ico (favicon file)
     * - public files (images, etc.)
     */
    '/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)',
  ],
};
