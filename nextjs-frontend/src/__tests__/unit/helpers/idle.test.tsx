/** `helpers/idle`: findIdleControl waits for a control that is really ready to be clicked. */
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { findIdleControl } from '@/__tests__/unit/helpers/idle';

function later(change: () => void, ms = 60) {
  setTimeout(change, ms);
}

describe('findIdleControl', () => {
  it('returns an idle control at once', async () => {
    render(<button>Save</button>);

    expect(await findIdleControl('button', 'Save')).toBe(
      screen.getByRole('button', { name: 'Save' })
    );
  });

  it('waits while the control is disabled', async () => {
    render(<button disabled>Save</button>);
    later(() => screen.getByRole('button', { name: 'Save' }).removeAttribute('disabled'));

    const control = await findIdleControl('button', 'Save');

    expect(control).toBeEnabled();
  });

  it('waits while the control is aria-disabled, which keeps it focusable and enabled', async () => {
    render(
      <button aria-disabled="true" onClick={() => undefined}>
        Save
      </button>
    );
    later(() =>
      screen.getByRole('button', { name: 'Save' }).setAttribute('aria-disabled', 'false')
    );

    const control = await findIdleControl('button', 'Save');

    expect(control).not.toHaveAttribute('aria-disabled', 'true');
  });

  it('waits while the control sits in a region that is still aria-busy', async () => {
    render(
      <section aria-busy="true" data-testid="region">
        <button>Save</button>
      </section>
    );
    later(() => screen.getByTestId('region').setAttribute('aria-busy', 'false'));

    const control = await findIdleControl('button', 'Save');

    expect(control.closest('[aria-busy="true"]')).toBeNull();
  });

  it('waits for the idle label when the pending label is shown first, and for a switch too', async () => {
    render(
      <div>
        <button>Checking…</button>
        <button role="switch" aria-checked="false" disabled>
          Auto-renewal
        </button>
      </div>
    );
    later(() => {
      screen.getByRole('button', { name: 'Checking…' }).textContent = 'Check';
      screen.getByRole('switch', { name: 'Auto-renewal' }).removeAttribute('disabled');
    });

    expect(await findIdleControl('button', 'Check')).toHaveTextContent('Check');
    expect(await findIdleControl('switch', 'Auto-renewal')).toBeEnabled();
  });

  it('finds the new control when the old one is replaced while the action finishes', async () => {
    // Plain DOM, not render(): the test replaces the node itself, as a remount does.
    const host = document.createElement('div');
    host.innerHTML = '<button disabled>Save</button>';
    document.body.append(host);
    const old = screen.getByRole('button', { name: 'Save' });
    later(() => {
      const replacement = document.createElement('button');
      replacement.textContent = 'Save';
      host.replaceChildren(replacement);
    });

    try {
      const control = await findIdleControl('button', 'Save');

      expect(control).not.toBe(old);
      expect(control).toBeInTheDocument();
    } finally {
      host.remove();
    }
  });

  it('fails with the reason when the control never becomes idle', async () => {
    render(<button disabled>Save</button>);

    await expect(findIdleControl('button', 'Save', { timeout: 150 })).rejects.toThrow(/enabled/i);
  });
});
