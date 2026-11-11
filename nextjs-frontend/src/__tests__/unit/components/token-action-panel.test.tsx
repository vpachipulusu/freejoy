import { StrictMode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { setupUser } from '@/__tests__/unit/helpers/user';
import { TokenActionPanel, type TokenActionCopy } from '@/components/alerts/token-action-panel';
import { ALERTS_FAILURE_MESSAGES } from '@/lib/alerts/outcomes';

const COPY: TokenActionCopy = {
  heading: 'Confirm things',
  intro: 'Press the button.',
  button: 'Do it',
  working: 'Doing it…',
  doneHeading: 'All done',
  doneBody: 'It worked.',
  doneLink: { href: '/next', label: 'Carry on' },
};
const TOKEN = 'abc.DEF-123_xyz';

function openAt(search: string) {
  window.history.replaceState(null, '', `/alerts/confirm${search}`);
}

function renderPanel(action = vi.fn().mockResolvedValue({ success: true })) {
  render(<TokenActionPanel copy={COPY} action={action} />);
  return action;
}

beforeEach(() => openAt(`?token=${TOKEN}`));
afterEach(() => window.history.replaceState(null, '', '/'));

describe('TokenActionPanel', () => {
  it('does nothing on load: only pressing the button calls the action', async () => {
    const action = renderPanel();

    expect(await screen.findByRole('button', { name: 'Do it' })).toBeInTheDocument();
    expect(action).not.toHaveBeenCalled();
  });

  it('removes the token from the address bar and never renders it', async () => {
    renderPanel();

    await screen.findByRole('button', { name: 'Do it' });

    expect(window.location.search).toBe('');
    expect(window.location.pathname).toBe('/alerts/confirm');
    expect(document.body.innerHTML).not.toContain(TOKEN);
  });

  it('sends the token from the link when the button is pressed, then shows the result', async () => {
    const action = renderPanel();

    await setupUser().click(await screen.findByRole('button', { name: 'Do it' }));

    expect(action).toHaveBeenCalledExactlyOnceWith(TOKEN);
    expect(await screen.findByRole('heading', { name: 'All done' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Carry on' })).toHaveAttribute('href', '/next');
    expect(document.body.innerHTML).not.toContain(TOKEN);
  });

  it('works under React Strict Mode: the button appears and the action gets the token once', async () => {
    const action = vi.fn().mockResolvedValue({ success: true });
    render(
      <StrictMode>
        <TokenActionPanel copy={COPY} action={action} />
      </StrictMode>
    );

    await setupUser().click(await screen.findByRole('button', { name: 'Do it' }));

    expect(action).toHaveBeenCalledExactlyOnceWith(TOKEN);
    expect(window.location.search).toBe('');
    expect(await screen.findByText('All done')).toBeInTheDocument();
  });

  it('keeps focus on the button, marked aria-disabled, while the action runs', async () => {
    let finish: (value: { success: true }) => void = () => {};
    const action = vi.fn().mockReturnValue(new Promise((resolve) => (finish = resolve)));
    renderPanel(action);
    const user = setupUser();

    await user.click(await screen.findByRole('button', { name: 'Do it' }));
    const working = screen.getByRole('button', { name: 'Doing it…' });
    await user.click(working);

    expect(working).toHaveAttribute('aria-disabled', 'true');
    expect(working).toHaveFocus();
    expect(action).toHaveBeenCalledTimes(1);
    finish({ success: true });
    await screen.findByText('All done');
  });

  it('keeps focus on the retry button after a temporary failure', async () => {
    renderPanel(
      vi.fn().mockResolvedValue({
        success: false,
        code: 'unavailable',
        error: ALERTS_FAILURE_MESSAGES.unavailable,
      })
    );

    await setupUser().click(await screen.findByRole('button', { name: 'Do it' }));
    await screen.findByRole('alert');

    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Do it' }));
  });

  it('does not wrap its alert in another live region', async () => {
    renderPanel();
    openAt('');
    render(<TokenActionPanel copy={COPY} action={vi.fn()} />);

    const alerts = await screen.findAllByRole('alert');

    expect(alerts.length).toBeGreaterThan(0);
    for (const alert of alerts) expect(alert.closest('[aria-live]')).toBeNull();
  });

  it('shows the action row after the heading so the button does not move when a message appears', async () => {
    renderPanel(
      vi.fn().mockResolvedValue({ success: false, code: 'unavailable', error: 'try again' })
    );
    const user = setupUser();
    const button = await screen.findByRole('button', { name: 'Do it' });
    const before = button.parentElement?.previousElementSibling;

    await user.click(button);
    const alert = await screen.findByRole('alert');

    expect(button.parentElement?.previousElementSibling).toBe(before);
    expect(button.compareDocumentPosition(alert) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('disables the button while the action runs', async () => {
    let finish: (value: { success: true }) => void = () => {};
    renderPanel(vi.fn().mockReturnValue(new Promise((resolve) => (finish = resolve))));

    await setupUser().click(await screen.findByRole('button', { name: 'Do it' }));

    expect(screen.getByRole('button', { name: 'Doing it…' })).toHaveAttribute(
      'aria-disabled',
      'true'
    );
    finish({ success: true });
    expect(await screen.findByText('All done')).toBeInTheDocument();
  });

  it('explains an expired or used link and offers no retry, only a way back', async () => {
    renderPanel(
      vi.fn().mockResolvedValue({
        success: false,
        code: 'invalid_link',
        error: ALERTS_FAILURE_MESSAGES.invalid_link,
      })
    );

    await setupUser().click(await screen.findByRole('button', { name: 'Do it' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(/not valid any more/i);
    expect(screen.queryByRole('button', { name: 'Do it' })).toBeNull();
    expect(screen.getByRole('link', { name: 'Back to alerts' })).toHaveAttribute('href', '/alerts');
  });

  it('keeps the token so a temporary failure can be retried', async () => {
    const action = vi
      .fn()
      .mockResolvedValueOnce({
        success: false,
        code: 'unavailable',
        error: ALERTS_FAILURE_MESSAGES.unavailable,
      })
      .mockResolvedValueOnce({ success: true });
    renderPanel(action);
    const user = setupUser();

    await user.click(await screen.findByRole('button', { name: 'Do it' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(ALERTS_FAILURE_MESSAGES.unavailable);
    await user.click(screen.getByRole('button', { name: 'Do it' }));

    expect(await screen.findByText('All done')).toBeInTheDocument();
    expect(action).toHaveBeenNthCalledWith(2, TOKEN);
  });

  it('shows a curated message when the action throws', async () => {
    renderPanel(vi.fn().mockRejectedValue(new Error('internal detail')));

    await setupUser().click(await screen.findByRole('button', { name: 'Do it' }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/something went wrong/i);
    expect(alert.textContent).not.toMatch(/internal detail/);
  });

  it('says the link is incomplete, and offers no button, when there is no token', async () => {
    openAt('');
    const action = renderPanel();

    expect(await screen.findByRole('alert')).toHaveTextContent(/link is incomplete/i);
    expect(screen.queryByRole('button')).toBeNull();
    expect(screen.getByRole('link', { name: 'Back to alerts' })).toBeInTheDocument();
    expect(action).not.toHaveBeenCalled();
  });
});
