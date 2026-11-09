/** Shown when a server action call itself fails (network drop, a deploy mid-request): nothing more is known. */
export const ACTION_FAILED_MESSAGE = 'Something went wrong. Please try again.';

export type ActionFailure = { success: false; error: string; reason?: 'offer_changed' };

/**
 * Awaits a server action call and turns a thrown error into a failure result, so the form or button can show
 * a message and move focus instead of leaving the admin with silence or an error page.
 */
export async function settleAction<T>(call: () => Promise<T>): Promise<T | ActionFailure> {
  try {
    return await call();
  } catch {
    return { success: false, error: ACTION_FAILED_MESSAGE };
  }
}
