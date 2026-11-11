import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { setupUser } from '@/__tests__/unit/helpers/user';
import { BillingToggle } from '@/components/pricing/billing-toggle';

/** billing toggle as an accessible radiogroup. */

const setup = (
  props: Partial<React.ComponentProps<typeof BillingToggle>> = {},
  onToggle = vi.fn()
) => {
  render(<BillingToggle billingPeriod="monthly" onToggle={onToggle} {...props} />);
  return {
    onToggle,
    monthly: screen.getByRole('radio', { name: /monthly/i }),
    yearly: screen.getByRole('radio', { name: /yearly/i }),
  };
};

describe('BillingToggle', () => {
  it('renders a named radiogroup with two radios', () => {
    setup();
    expect(screen.getByRole('radiogroup', { name: /billing period/i })).toBeInTheDocument();
    expect(screen.getAllByRole('radio')).toHaveLength(2);
  });

  it('marks only the active period as checked', () => {
    const { monthly, yearly } = setup({ billingPeriod: 'yearly' });
    expect(yearly).toHaveAttribute('aria-checked', 'true');
    expect(monthly).toHaveAttribute('aria-checked', 'false');
  });

  it('uses roving tabIndex', () => {
    const { monthly, yearly } = setup({ billingPeriod: 'yearly' });
    expect(yearly).toHaveAttribute('tabindex', '0');
    expect(monthly).toHaveAttribute('tabindex', '-1');
  });

  it('calls onToggle on click', async () => {
    const user = setupUser();
    const { onToggle, yearly } = setup();
    await user.click(yearly);
    expect(onToggle).toHaveBeenCalledWith('yearly');
  });

  it.each(['{ArrowRight}', '{ArrowDown}', '{End}'])(
    '%s selects yearly from monthly',
    async (key) => {
      const { onToggle, monthly, yearly } = setup();
      monthly.focus();
      await setupUser().keyboard(key);
      expect(onToggle).toHaveBeenCalledWith('yearly');
      expect(yearly).toHaveFocus();
    }
  );

  it.each(['{ArrowLeft}', '{ArrowUp}', '{Home}'])('%s selects monthly from yearly', async (key) => {
    const user = setupUser();
    const { onToggle, monthly, yearly } = setup({ billingPeriod: 'yearly' });
    yearly.focus();
    await user.keyboard(key);
    expect(onToggle).toHaveBeenCalledWith('monthly');
    expect(monthly).toHaveFocus();
  });

  it('wraps around with arrow keys', async () => {
    const user = setupUser();
    const { onToggle, monthly } = setup();
    monthly.focus();
    await user.keyboard('{ArrowLeft}');
    expect(onToggle).toHaveBeenCalledWith('yearly');
  });

  it('ignores other keys', async () => {
    const user = setupUser();
    const { onToggle, monthly } = setup();
    monthly.focus();
    await user.keyboard('a');
    expect(onToggle).not.toHaveBeenCalled();
  });

  it('sizes each option to at least 44px high and 96px wide', () => {
    const { monthly, yearly } = setup();
    for (const radio of [monthly, yearly]) {
      expect(radio.className).toContain('min-h-11');
      expect(radio.className).toContain('min-w-24');
    }
  });

  it('fills the selected option with foreground and background text, not the cta colour', () => {
    const { monthly, yearly } = setup();
    expect(monthly.className).toContain('bg-foreground');
    expect(monthly.className).toContain('text-background');
    expect(monthly.className).not.toContain('bg-cta');
    expect(yearly.className).not.toContain('bg-foreground');
  });

  it('respects reduced motion', () => {
    const { monthly } = setup();
    expect(monthly.className).toContain('motion-reduce:transition-none');
  });

  it('shows the saving as plain text inside the yearly option', () => {
    const { yearly } = setup({ yearlySavingsPercent: 17 });
    expect(yearly).toHaveTextContent('Save 17%');
  });

  it.each([undefined, 0, -5, Number.NaN])('hides the saving for %s', (value) => {
    setup({ yearlySavingsPercent: value });
    expect(screen.queryByText(/save/i)).not.toBeInTheDocument();
  });

  it('leaves modified arrow keys to the browser', async () => {
    const user = setupUser();
    const { onToggle, monthly } = setup();
    monthly.focus();
    await user.keyboard('{Alt>}{ArrowRight}{/Alt}');
    expect(onToggle).not.toHaveBeenCalled();
  });

  it('updates aria-checked and tabindex when the period prop changes', () => {
    const { rerender } = render(<BillingToggle billingPeriod="monthly" onToggle={vi.fn()} />);
    rerender(<BillingToggle billingPeriod="yearly" onToggle={vi.fn()} />);
    const yearly = screen.getByRole('radio', { name: /yearly/i });
    expect(yearly).toHaveAttribute('aria-checked', 'true');
    expect(yearly).toHaveAttribute('tabindex', '0');
  });

  it('leaves the focus style to the global outline', () => {
    const { monthly } = setup();
    expect(monthly.className).not.toMatch(/outline-none|(^|[\s:])ring(-|\s|$)/);
  });

  it('gives the yearly radio the accessible name "Yearly Save 17%"', () => {
    setup({ yearlySavingsPercent: 17 });
    expect(screen.getByRole('radio', { name: 'Yearly Save 17%' })).toBeInTheDocument();
  });

  it('moves focus without calling onToggle when the target is already selected', async () => {
    const user = setupUser();
    const { onToggle, monthly } = setup();
    monthly.focus();
    await user.keyboard('{Home}');
    expect(onToggle).not.toHaveBeenCalled();
    expect(monthly).toHaveFocus();
  });

  it('does not call onToggle for End on yearly', async () => {
    const user = setupUser();
    const { onToggle, yearly } = setup({ billingPeriod: 'yearly' });
    yearly.focus();
    await user.keyboard('{End}');
    expect(onToggle).not.toHaveBeenCalled();
    expect(yearly).toHaveFocus();
  });

  it('wraps ArrowRight from yearly to monthly', async () => {
    const user = setupUser();
    const { onToggle, monthly, yearly } = setup({ billingPeriod: 'yearly' });
    yearly.focus();
    await user.keyboard('{ArrowRight}');
    expect(onToggle).toHaveBeenCalledWith('monthly');
    expect(monthly).toHaveFocus();
  });

  it('skips Shift-modified arrow keys', async () => {
    const user = setupUser();
    const { onToggle, monthly } = setup();
    monthly.focus();
    await user.keyboard('{Shift>}{ArrowRight}{/Shift}');
    expect(onToggle).not.toHaveBeenCalled();
  });
});
