import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { setupUser } from '@/__tests__/unit/helpers/user';
import { Pagination, getRange } from '@/components/ui/pagination';

/** pagination with 44px prev/next and "Showing x to y of z". */

describe('getRange', () => {
  it('computes first and last page ranges', () => {
    expect(getRange(1, 25, 312)).toEqual({ page: 1, from: 1, to: 25, pageCount: 13 });
    expect(getRange(13, 25, 312)).toEqual({ page: 13, from: 301, to: 312, pageCount: 13 });
  });

  it('guards a non-positive page size', () => {
    expect(getRange(1, 0, 10).pageCount).toBe(10);
  });

  it('handles an empty list', () => {
    expect(getRange(1, 25, 0)).toEqual({ page: 1, from: 0, to: 0, pageCount: 1 });
  });

  it('clamps an out-of-range page', () => {
    expect(getRange(99, 25, 30)).toMatchObject({ page: 2, from: 26, to: 30 });
    expect(getRange(0, 25, 30)).toMatchObject({ page: 1, from: 1 });
  });
});

describe('Pagination', () => {
  const setup = (page = 2, onPageChange = vi.fn()) => {
    render(<Pagination page={page} pageSize={25} total={312} onPageChange={onPageChange} />);
    return onPageChange;
  };

  it('renders a labelled nav with the Showing text', () => {
    setup(1);
    expect(screen.getByRole('navigation', { name: 'Pagination' })).toBeInTheDocument();
    expect(screen.getByText('Showing 1 to 25 of 312')).toBeInTheDocument();
  });

  it('uses 44px previous and next buttons', () => {
    setup();
    for (const name of ['Previous page', 'Next page']) {
      const cls = screen.getByRole('button', { name }).className;
      expect(cls).toContain('h-11');
      expect(cls).toContain('w-11');
    }
  });

  it('shows the current page as plain text, not as a set item', () => {
    setup(2);
    expect(screen.getByText('Page 2 of 13')).not.toHaveAttribute('aria-current');
  });

  it('calls onPageChange with neighbouring pages', async () => {
    const user = setupUser();
    const onPageChange = setup(2);
    await user.click(screen.getByRole('button', { name: 'Next page' }));
    await user.click(screen.getByRole('button', { name: 'Previous page' }));
    expect(onPageChange).toHaveBeenNthCalledWith(1, 3);
    expect(onPageChange).toHaveBeenNthCalledWith(2, 1);
  });

  it('keeps focus on a disabled button and ignores its clicks', async () => {
    const user = setupUser();
    const onPageChange = vi.fn();
    render(<Pagination page={1} pageSize={25} total={312} onPageChange={onPageChange} />);
    const prev = screen.getByRole('button', { name: 'Previous page' });
    await user.click(prev);
    expect(onPageChange).not.toHaveBeenCalled();
    expect(prev).not.toBeDisabled();
  });

  it('disables previous on the first page and next on the last', () => {
    const { unmount } = render(
      <Pagination page={1} pageSize={25} total={312} onPageChange={() => {}} />
    );
    expect(screen.getByRole('button', { name: 'Previous page' })).toHaveAttribute(
      'aria-disabled',
      'true'
    );
    unmount();
    render(<Pagination page={13} pageSize={25} total={312} onPageChange={() => {}} />);
    expect(screen.getByRole('button', { name: 'Next page' })).toHaveAttribute(
      'aria-disabled',
      'true'
    );
    expect(screen.getByText('Showing 301 to 312 of 312')).toBeInTheDocument();
  });

  it('announces range changes politely', () => {
    setup(1);
    expect(screen.getByText('Showing 1 to 25 of 312')).toHaveAttribute('aria-live', 'polite');
  });

  it('disables both buttons for an empty list', () => {
    render(<Pagination page={1} pageSize={25} total={0} onPageChange={() => {}} />);
    expect(screen.getByText('Showing 0 to 0 of 0')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Previous page' })).toHaveAttribute(
      'aria-disabled',
      'true'
    );
    expect(screen.getByRole('button', { name: 'Next page' })).toHaveAttribute(
      'aria-disabled',
      'true'
    );
  });
});
