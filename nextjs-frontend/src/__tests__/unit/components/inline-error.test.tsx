import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { setupUser } from '@/__tests__/unit/helpers/user';

const mocks = vi.hoisted(() => ({ refresh: vi.fn() }));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ refresh: mocks.refresh }),
  usePathname: () => '/',
  useSearchParams: () => new URLSearchParams(),
}));

import { CatalogueUnavailablePage, InlineError } from '@/components/catalogue/inline-error';
import { RetryButton } from '@/components/catalogue/retry-button';
import { CATALOGUE_UNAVAILABLE_MESSAGE } from '@/lib/catalogue/errors';

beforeEach(() => mocks.refresh.mockClear());

describe('InlineError', () => {
  it('announces a curated message as an alert', () => {
    render(<InlineError />);

    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('The freebies are not loading');
    expect(alert).toHaveTextContent(CATALOGUE_UNAVAILABLE_MESSAGE);
  });

  it('accepts its own wording and extra classes', () => {
    render(<InlineError title="Oops" message="Try later" className="mt-10" />);

    expect(screen.getByRole('alert')).toHaveClass('mt-10');
    expect(screen.getByText('Oops')).toBeInTheDocument();
    expect(screen.getByText('Try later')).toBeInTheDocument();
  });

  it('hides the decorative icon from screen readers', () => {
    const { container } = render(<InlineError />);

    expect(container.querySelector('svg')).toHaveAttribute('aria-hidden', 'true');
  });
});

describe('RetryButton', () => {
  it('is a real button, reachable by keyboard, that re-runs the server render', async () => {
    const user = setupUser();
    render(<RetryButton />);

    await user.tab();
    const button = screen.getByRole('button', { name: 'Try again' });
    expect(button).toHaveFocus();
    expect(button).toHaveAttribute('type', 'button');

    await user.keyboard('{Enter}');
    expect(mocks.refresh).toHaveBeenCalledTimes(1);
  });

  it('also works with the space key and a click', async () => {
    const user = setupUser();
    render(<RetryButton />);

    await user.tab();
    await user.keyboard(' ');
    await user.click(screen.getByRole('button', { name: /try/i }));

    expect(mocks.refresh).toHaveBeenCalledTimes(2);
  });

  it('keeps focus and ignores a second click while the refresh is running', async () => {
    mocks.refresh.mockImplementation(() => new Promise<void>(() => {}));
    const user = setupUser();
    render(<RetryButton />);

    await user.tab();
    await user.keyboard('{Enter}');

    const busy = await screen.findByRole('button', { name: 'Trying again…' });
    expect(busy).toHaveAttribute('aria-disabled', 'true');
    expect(busy).toHaveFocus();
    await user.click(busy);
    expect(mocks.refresh).toHaveBeenCalledTimes(1);
    mocks.refresh.mockReset();
  });

  describe('focus after a retry', () => {
    const Page = ({ failed }: { failed: boolean }) => (
      <>
        <main id="main-content" tabIndex={-1}>
          {failed ? <RetryButton /> : <p>Loaded</p>}
        </main>
      </>
    );

    // The previous test's cleanup unmounts a used button, whose deferred focus move must not land here.
    beforeEach(() => new Promise((resolve) => setTimeout(resolve, 20)));

    it('moves focus to the main landmark when the retry succeeds and the button disappears', async () => {
      const user = setupUser();
      const { rerender } = render(<Page failed />);
      await user.tab();
      await user.keyboard('{Enter}');

      rerender(<Page failed={false} />);

      await waitFor(() => expect(screen.getByRole('main')).toHaveFocus());
    });

    it('leaves focus alone while the button is still on the page', async () => {
      const user = setupUser();
      render(<Page failed />);
      await user.tab();
      await user.keyboard('{Enter}');

      await new Promise((resolve) => setTimeout(resolve, 20));

      expect(screen.getByRole('button', { name: /try/i })).toHaveFocus();
      expect(screen.getByRole('main')).not.toHaveFocus();
    });

    it('does not steal focus when it unmounts without having been used', async () => {
      const { rerender } = render(<Page failed />);
      rerender(<Page failed={false} />);

      await new Promise((resolve) => setTimeout(resolve, 20));

      expect(screen.getByRole('main')).not.toHaveFocus();
    });

    it('does not move focus that already went somewhere else', async () => {
      const user = setupUser();
      const { rerender } = render(
        <>
          <Page failed />
          <input aria-label="other" />
        </>
      );
      await user.tab();
      await user.keyboard('{Enter}');
      rerender(
        <>
          <Page failed={false} />
          <input aria-label="other" />
        </>
      );
      screen.getByLabelText('other').focus();

      await new Promise((resolve) => setTimeout(resolve, 20));

      expect(screen.getByLabelText('other')).toHaveFocus();
    });
  });

  it('is large enough to tap', () => {
    render(<RetryButton />);

    expect(screen.getByRole('button')).toHaveClass('min-h-11');
  });
});

describe('CatalogueUnavailablePage', () => {
  it('keeps a heading so the page still has a title, and the alert with its retry', () => {
    render(<CatalogueUnavailablePage heading="Beauty freebies" />);

    expect(screen.getByRole('heading', { level: 1, name: 'Beauty freebies' })).toBeInTheDocument();
    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });

  it('uses a generic heading by default', () => {
    render(<CatalogueUnavailablePage />);

    expect(screen.getByRole('heading', { level: 1, name: 'Freebies' })).toBeInTheDocument();
  });
});
