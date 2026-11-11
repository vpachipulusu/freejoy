import userEvent from '@testing-library/user-event';

type SetupOptions = NonNullable<Parameters<typeof userEvent.setup>[0]>;

/**
 * `userEvent.setup()` without the pause between actions.
 *
 * user-event waits `setTimeout(0)` after every keystroke and click by default. On Windows a zero-delay timer
 * can wait for the next scheduler tick (about 25 ms per keystroke measured here), so typing a 16-character
 * email and password cost about 0.4 s before anything under test ran, and under machine load a test of one
 * sign-in took 2 s and more (FREEJ-64: `findByLabelText` gave up after its 1 s default and whole tests hit the
 * 5 s limit). Every keystroke still dispatches the same keydown, input and keyup events and React still renders
 * after each one; only the idle wait between them is gone. Tests that need a real pause pass `delay` themselves.
 *
 * Tests use this and never `userEvent.*` directly (an ESLint rule in `eslint.config.mjs` enforces it).
 */
export function setupUser(options: SetupOptions = {}) {
  return userEvent.setup({ delay: null, ...options });
}

/** The type of what `setupUser()` returns, for helpers that take the user as a parameter. */
export type TestUser = ReturnType<typeof setupUser>;
