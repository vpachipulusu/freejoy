import { expect, vi } from 'vitest';
import { screen } from '@testing-library/react';

/**
 * The control once the action that disabled it (or renamed it, "Checking…") has finished.
 *
 * A result can be on screen while the transition or pending flag that produced it is still closing, so a
 * synchronous `getByRole('button', { name })` for the idle control, taken right after the result appeared,
 * can find nothing (the button still reads "Checking…") or a disabled button that ignores the click. Under
 * machine load that window is long enough for a test to land in it (FREEJ-64). Wait for the idle, enabled
 * control instead of reading it once.
 */
export async function findIdleControl(role: 'button' | 'switch', name: string | RegExp) {
  const control = await screen.findByRole(role, { name });
  await vi.waitFor(() => expect(control).toBeEnabled());
  return control;
}
