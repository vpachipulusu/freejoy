import { expect } from 'vitest';
import { screen, waitFor } from '@testing-library/react';

/**
 * The control once the action that disabled it (or renamed it, "Checking…") has finished.
 *
 * A result can be on screen while the transition or pending flag that produced it is still closing, so a
 * synchronous `getByRole('button', { name })` for the idle control, taken right after the result appeared,
 * can find nothing (the button still reads "Checking…"), a control that ignores the click (disabled, or
 * `aria-disabled="true"`, which most pending controls here use so they stay focusable) or one inside a region
 * that is still `aria-busy`. Under machine load that window is long enough for a test to land in it (FREEJ-64).
 *
 * The whole lookup is retried, not only the state of one node, so a control that is replaced while the action
 * finishes (a remount) is found again. Returns the idle control; fails with the last reason after `timeout`.
 */
export async function findIdleControl(
  role: 'button' | 'switch',
  name: string | RegExp,
  options: { timeout?: number } = {}
): Promise<HTMLElement> {
  return waitFor(() => {
    const control = screen.getByRole(role, { name });
    expect(control).toBeEnabled();
    expect(control).not.toHaveAttribute('aria-disabled', 'true');
    expect(control.closest('[aria-busy="true"]')).toBeNull();
    return control;
  }, options);
}
