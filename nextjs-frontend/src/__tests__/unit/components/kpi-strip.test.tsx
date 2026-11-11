import { describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { setupUser } from '@/__tests__/unit/helpers/user';
import { KpiStrip, type KpiCell } from '@/components/dashboard/kpi-strip';

/** fixtures only, no data API exists yet. */

const READY: KpiCell[] = [
  {
    id: 'revenue',
    label: 'Revenue',
    state: 'ready',
    value: '£4,280',
    delta: { direction: 'up', text: '12%', comparison: 'the previous 7 days' },
    trend: [3, 5, 4, 8, 7, 9, 12],
  },
  {
    id: 'orders',
    label: 'Orders',
    state: 'ready',
    value: '86',
    delta: { direction: 'down', text: '4%', comparison: 'the previous 7 days' },
  },
  {
    id: 'conversion',
    label: 'Conversion',
    state: 'ready',
    value: '2.4%',
    delta: { direction: 'flat', text: '0%', comparison: 'the previous 7 days' },
  },
  { id: 'aov', label: 'Average order', state: 'ready', value: '£49.80', delta: null },
];

function cell(label: string): HTMLElement {
  return screen.getByRole('group', { name: label });
}

describe('KpiStrip layout', () => {
  it('is a labelled list, 2 columns by default and 4 from 760px', () => {
    render(<KpiStrip cells={READY} />);

    const strip = screen.getByRole('list', { name: 'Key figures' });
    expect(strip).toHaveClass('grid-cols-2', 'min-[760px]:grid-cols-4');
    expect(within(strip).getAllByRole('listitem')).toHaveLength(4);
  });

  it('is one token-coloured panel', () => {
    render(<KpiStrip cells={READY} />);

    expect(screen.getByRole('list')).toHaveClass('bg-card', 'border-border', 'rounded-xl');
  });
});

describe('KpiStrip ready cells', () => {
  it('shows label and tabular value', () => {
    render(<KpiStrip cells={READY} />);

    expect(within(cell('Revenue')).getByText('£4,280')).toHaveClass('tabular-nums');
  });

  it.each([
    ['Revenue', 'up 12% on the previous 7 days'],
    ['Orders', 'down 4% on the previous 7 days'],
    ['Conversion', 'no change on the previous 7 days'],
  ])('%s delta has screen-reader text', (label, spoken) => {
    render(<KpiStrip cells={READY} />);

    expect(within(cell(label)).getByText(spoken)).toHaveClass('sr-only');
  });

  it('hides the visible delta and its icon from assistive tech', () => {
    render(<KpiStrip cells={READY} />);

    const revenue = cell('Revenue');
    expect(within(revenue).getByText('12%').closest('[aria-hidden="true"]')).not.toBeNull();
  });

  it('says so when there is no comparison', () => {
    render(<KpiStrip cells={READY} />);

    expect(within(cell('Average order')).getByText('No comparison yet')).toBeInTheDocument();
  });

  it('renders the sparkline as decorative only', () => {
    const { container } = render(<KpiStrip cells={READY} />);

    expect(container.querySelector('[data-testid="kpi-sparkline-revenue"]')).toHaveAttribute(
      'aria-hidden',
      'true'
    );
    expect(container.querySelector('[data-testid="kpi-sparkline-orders"]')).toBeNull();
  });

  it('does not draw a sparkline for a single point', () => {
    const { container } = render(
      <KpiStrip cells={[{ id: 'a', label: 'A', state: 'ready', value: '1', trend: [5] }]} />
    );

    expect(container.querySelector('[data-testid="kpi-sparkline-a"]')).toBeNull();
  });

  it('draws a flat series without dividing by zero', () => {
    const { container } = render(
      <KpiStrip cells={[{ id: 'a', label: 'A', state: 'ready', value: '1', trend: [5, 5, 5] }]} />
    );

    const path = container.querySelector('[data-testid="kpi-sparkline-a"] path');
    expect(path?.getAttribute('d')).not.toMatch(/NaN/);
  });
});

describe('KpiStrip loading', () => {
  it('keeps the label, skeletons the value and sparkline and marks the cell busy', () => {
    const { container } = render(
      <KpiStrip cells={[{ id: 'revenue', label: 'Revenue', state: 'loading' }]} />
    );

    const revenue = cell('Revenue');
    expect(revenue).toHaveAttribute('aria-busy', 'true');
    expect(within(revenue).getByText('Revenue')).toBeInTheDocument();
    expect(container.querySelectorAll('.motion-safe\\:animate-pulse')).toHaveLength(2);
  });
});

describe('KpiStrip empty', () => {
  it('shows a dash and the empty text', () => {
    render(
      <KpiStrip
        cells={[{ id: 'orders', label: 'Orders', state: 'empty', emptyText: 'No orders yet' }]}
      />
    );

    const orders = cell('Orders');
    expect(within(orders).getByText('—')).toHaveAttribute('aria-hidden', 'true');
    expect(within(orders).getByText('No orders yet')).toBeInTheDocument();
  });

  it('renders an empty action link at least 44px tall', () => {
    render(
      <KpiStrip
        cells={[
          {
            id: 'orders',
            label: 'Orders',
            state: 'empty',
            emptyText: 'No orders yet',
            emptyAction: { label: 'Add a product', href: '/dashboard/products/new' },
          },
        ]}
      />
    );

    const link = screen.getByRole('link', { name: 'Add a product' });
    expect(link).toHaveAttribute('href', '/dashboard/products/new');
    expect(link).toHaveClass('min-h-11');
  });

  it('falls back to default empty text', () => {
    render(<KpiStrip cells={[{ id: 'orders', label: 'Orders', state: 'empty' }]} />);

    expect(screen.getByText('No data yet')).toBeInTheDocument();
  });
});

describe('KpiStrip error', () => {
  it('shows a retry for the failed cell only and leaves the others intact', async () => {
    const user = setupUser();
    const onRetry = vi.fn();
    const cells: KpiCell[] = [
      READY[0],
      { id: 'orders', label: 'Orders', state: 'error', onRetry },
      READY[2],
    ];
    render(<KpiStrip cells={cells} />);

    const orders = cell('Orders');
    expect(within(orders).getByText('Could not load')).toBeInTheDocument();
    const retry = within(orders).getByRole('button', { name: 'Retry Orders' });
    expect(retry).toHaveClass('min-h-11');
    expect(screen.getAllByRole('button')).toHaveLength(1);
    expect(within(cell('Revenue')).getByText('£4,280')).toBeInTheDocument();
    expect(within(cell('Conversion')).getByText('2.4%')).toBeInTheDocument();

    await user.click(retry);
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('announces the error', () => {
    render(
      <KpiStrip cells={[{ id: 'orders', label: 'Orders', state: 'error', onRetry: vi.fn() }]} />
    );

    expect(within(cell('Orders')).getByRole('alert')).toHaveTextContent('Could not load');
  });

  it('shows no retry button when no handler is given', () => {
    render(<KpiStrip cells={[{ id: 'orders', label: 'Orders', state: 'error' }]} />);

    expect(screen.queryByRole('button')).toBeNull();
    expect(screen.getByText('Could not load')).toBeInTheDocument();
  });
});
