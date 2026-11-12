import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Spinner } from '@/components/ui/spinner';

/** the shared spinner is announced once, uses theme tokens and stops spinning under reduced motion. */

describe('Spinner', () => {
  it('is a polite status region named "Loading"', () => {
    render(<Spinner />);

    const status = screen.getByRole('status', { name: 'Loading' });
    expect(status).toHaveAttribute('aria-live', 'polite');
    expect(status).toHaveTextContent('Loading');
    expect(screen.getByText('Loading')).toHaveClass('sr-only');
  });

  it('takes another accessible name', () => {
    render(<Spinner label="Saving offer" />);

    expect(screen.getByRole('status', { name: 'Saving offer' })).toBeInTheDocument();
  });

  it('spins only under motion-safe and is static under reduced motion', () => {
    const { container } = render(<Spinner />);
    const ring = container.querySelector('svg') as SVGElement;

    expect(ring).toHaveClass('motion-safe:animate-spin', 'motion-reduce:animate-none');
    expect(ring.getAttribute('class')).not.toMatch(/(^|\s)animate-spin/);
    expect(ring).toHaveAttribute('aria-hidden', 'true');
  });

  it('is drawn in a theme token colour, never a hard-coded one', () => {
    const { container } = render(<Spinner />);

    expect(screen.getByRole('status')).toHaveClass('text-accent-ink');
    expect(container.innerHTML).not.toMatch(/#[0-9a-f]{3,8}\b|rgb\(|hsl\(/i);
    for (const shape of container.querySelectorAll('circle, path')) {
      expect(shape.getAttribute('stroke')).toBe('currentColor');
    }
  });

  it('keeps the size it is given', () => {
    render(<Spinner size="lg" className="mx-auto" />);

    expect(screen.getByRole('status')).toHaveClass('h-10', 'w-10', 'mx-auto');
  });

  it('can be decorative: no role, no name, hidden from assistive technology', () => {
    const { container } = render(<Spinner decorative />);

    expect(screen.queryByRole('status')).toBeNull();
    expect(container.firstElementChild).toHaveAttribute('aria-hidden', 'true');
    expect(container).not.toHaveTextContent('Loading');
  });

  it('forwards props such as data attributes', () => {
    render(<Spinner data-testid="spinner" />);

    expect(screen.getByTestId('spinner')).toBe(screen.getByRole('status'));
  });
});
