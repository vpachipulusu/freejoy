/**
 * Name of the httpOnly cookie that carries the Firebase session. Kept in its own dependency-free module so a
 * route that only needs the name (for example `/api/auth/session-status`) does not import the backend client.
 * `@/lib/api/server` re-exports it.
 */
export const SESSION_COOKIE_NAME = 'session';
