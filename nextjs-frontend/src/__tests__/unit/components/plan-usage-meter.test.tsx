import { describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { setupUser } from '@/__tests__/unit/helpers/user';
import { PlanUsageMeter, type UsageMeterItem } from '@/components/dashboard/plan-usage-meter';

/** fixtures only. Meter semantics never rely on colour alone. */

const ITEMS: UsageMeterItem[] = [
  { id: 'products', label: 'Products', used: 12, limit: 50 },
  { id: 'staff', label: 'Staff seats', used: 4, limit: 5 },
  { id: 'members', label: 'Members', used: 1, limit: 1 },
];

describe('PlanUsageMeter', () => {
  it('renders a labelled group with one meter per item and text values', () => {
    render(<PlanUsageMeter items={ITEMS} />);

    expect(screen.getByRole('group', { name: 'Plan usage' })).toBeInTheDocument();
    const products = screen.getByRole('meter', { name: 'Products' });
    expect(products).toHaveAttribute('aria-valuemin', '0');
    expect(products).toHaveAttribute('aria-valuemax', '50');
    expect(products).toHaveAttribute('aria-valuenow', '12');
    expect(products).toHaveAttribute('aria-valuetext', '12 of 50 used');
  });

  it('shows the count as visible text too', () => {
    render(<PlanUsageMeter items={ITEMS} />);

    expect(screen.getByText('12 of 50')).toHaveClass('tabular-nums');
  });

  it('shows a Near limit warn pill from 80% up', () => {
    render(<PlanUsageMeter items={ITEMS} />);

    const staff = screen.getByRole('meter', { name: 'Staff seats' });
    expect(staff).toHaveAttribute('aria-valuetext', '4 of 5 used, near limit');
    expect(screen.getByText('Near limit').closest('span')).toHaveClass(
      'bg-warn',
      'text-warn-foreground'
    );
  });

  it('shows a crit Limit reached pill at the limit', () => {
    render(<PlanUsageMeter items={ITEMS} />);

    const stores = screen.getByRole('meter', { name: 'Members' });
    expect(stores).toHaveAttribute('aria-valuetext', '1 of 1 used, limit reached');
    const pill = screen.getByText('Limit reached').closest('span');
    expect(pill).toHaveClass('bg-crit', 'text-crit-foreground');
    expect(pill?.querySelector('svg')).toHaveAttribute('aria-hidden', 'true');
  });

  it('shows no pill under 80%', () => {
    render(<PlanUsageMeter items={[ITEMS[0]]} />);

    expect(screen.queryByText('Near limit')).toBeNull();
    expect(screen.queryByText('Limit reached')).toBeNull();
  });

  it('clamps usage above the limit to a full bar but keeps the real count', () => {
    render(<PlanUsageMeter items={[{ id: 'p', label: 'Products', used: 60, limit: 50 }]} />);

    const meter = screen.getByRole('meter', { name: 'Products' });
    expect(meter).toHaveAttribute('aria-valuenow', '50');
    expect(meter).toHaveAttribute('aria-valuetext', '60 of 50 used, limit reached');
    expect(screen.getByText('Limit reached')).toBeInTheDocument();
    expect(meter.firstElementChild).toHaveStyle({ width: '100%' });
  });

  it('sizes the fill by percentage and animates only when motion is allowed', () => {
    render(<PlanUsageMeter items={[ITEMS[0]]} />);

    const fill = screen.getByRole('meter', { name: 'Products' }).firstElementChild;
    expect(fill).toHaveStyle({ width: '24%' });
    expect(fill).toHaveClass('motion-safe:transition-[width]');
  });

  it('treats an unlimited plan as text, not a meter', () => {
    render(<PlanUsageMeter items={[{ id: 'p', label: 'Products', used: 12, limit: null }]} />);

    expect(screen.queryByRole('meter')).toBeNull();
    expect(screen.getByText('12 used, unlimited')).toBeInTheDocument();
  });

  it('treats a zero limit as limit reached without dividing by zero', () => {
    render(<PlanUsageMeter items={[{ id: 'p', label: 'Products', used: 0, limit: 0 }]} />);

    expect(screen.getByRole('meter', { name: 'Products' })).toHaveAttribute('aria-valuenow', '0');
    expect(screen.getByText('Limit reached')).toBeInTheDocument();
  });

  it('renders a 44px compare button when given a handler', async () => {
    const user = setupUser();
    const onCompare = vi.fn();
    render(
      <PlanUsageMeter
        items={ITEMS}
        compareAction={{ label: 'Compare plans', onClick: onCompare }}
      />
    );

    const button = screen.getByRole('button', { name: 'Compare plans' });
    expect(button).toHaveClass('min-h-11');
    await user.click(button);
    expect(onCompare).toHaveBeenCalledTimes(1);
  });

  it('renders a compare link when href is given', () => {
    render(
      <PlanUsageMeter items={ITEMS} compareAction={{ label: 'Compare plans', href: '/pricing' }} />
    );

    const link = screen.getByRole('link', { name: 'Compare plans' });
    expect(link).toHaveAttribute('href', '/pricing');
    expect(link).toHaveClass('min-h-11');
  });
});

describe('PlanUsageMeter states', () => {
  it('shows skeletons and a busy group while loading', () => {
    const { container } = render(<PlanUsageMeter state="loading" items={[]} />);

    expect(screen.getByRole('group', { name: 'Plan usage' })).toHaveAttribute('aria-busy', 'true');
    expect(screen.queryByRole('meter')).toBeNull();
    expect(container.querySelectorAll('.motion-safe\\:animate-pulse').length).toBeGreaterThan(0);
  });

  it('shows an empty message when there are no limits', () => {
    render(<PlanUsageMeter items={[]} />);

    expect(screen.getByText('No plan limits to show')).toBeInTheDocument();
  });

  it('shows an inline error with retry', async () => {
    const user = setupUser();
    const onRetry = vi.fn();
    render(<PlanUsageMeter state="error" items={[]} onRetry={onRetry} />);

    expect(
      within(screen.getByRole('alert')).getByText('Could not load plan usage')
    ).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Retry plan usage' }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('shows the error without a retry button when there is no handler', () => {
    render(<PlanUsageMeter state="error" items={[]} />);

    expect(screen.queryByRole('button')).toBeNull();
  });
});
