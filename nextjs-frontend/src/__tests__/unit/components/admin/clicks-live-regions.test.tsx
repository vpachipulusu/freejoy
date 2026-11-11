/**
 * The click report's live regions and focus: the one status region exists before its text and the streamed pieces
 * write into that same element; the results heading takes focus after the report changes (and follows the form);
 * Retry keeps focus on the page.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { setupUser } from '@/__tests__/unit/helpers/user';

const mocks = vi.hoisted(() => ({ refresh: vi.fn() }));

vi.mock('next/navigation', () => ({ useRouter: () => ({ refresh: mocks.refresh }) }));
vi.mock('@/app/actions/admin/merchant-clicks-actions', () => ({
  exportMerchantClicksAction: vi.fn(),
}));

import { ResultsFocus } from '@/app/(admin)/admin/_components/results-focus';
import { MerchantClicksLoading } from '@/app/(admin)/admin/analytics/merchant-clicks-results';
import {
  RETRY_PENDING_MESSAGE,
  RetryClicksButton,
} from '@/app/(admin)/admin/analytics/retry-clicks-button';
import { CLICKS_STATUS_ID, StatusAnnouncer } from '@/app/(admin)/admin/analytics/status-announcer';

const Region = () => <p role="status" id={CLICKS_STATUS_ID} />;

describe('StatusAnnouncer', () => {
  it('writes into a status region that was already in the page, and keeps the same element', () => {
    const { rerender } = render(<Region />);
    const region = screen.getByRole('status');
    expect(region).toBeEmptyDOMElement();

    rerender(
      <>
        <Region />
        <StatusAnnouncer message="Acme: 3 clicks" />
      </>
    );
    expect(screen.getByRole('status')).toBe(region);
    expect(region).toHaveTextContent('Acme: 3 clicks');

    rerender(
      <>
        <Region />
        <StatusAnnouncer message="Acme: 5 clicks" />
      </>
    );
    expect(screen.getByRole('status')).toBe(region);
    expect(region).toHaveTextContent('Acme: 5 clicks');
    expect(region.textContent).not.toContain('3 clicks');

    rerender(<Region />);
    expect(region).toBeEmptyDOMElement();
  });

  it('renders nothing of its own and creates no second live region', () => {
    render(
      <>
        <Region />
        <StatusAnnouncer message="hello" />
      </>
    );

    expect(screen.getAllByRole('status')).toHaveLength(1);
  });

  it('shows the loading line as plain text and announces it through the region', () => {
    render(
      <>
        <Region />
        <MerchantClicksLoading />
      </>
    );

    expect(screen.getAllByText('Loading the click report…')).toHaveLength(2);
    expect(screen.getByRole('status')).toHaveTextContent('Loading the click report…');
    expect(screen.getAllByRole('status')).toHaveLength(1);
  });
});

describe('ResultsFocus after the report changes', () => {
  function Page({ focusKey }: { focusKey: string }) {
    return (
      <section>
        <form aria-label="Choose a merchant">
          <button type="submit">Show report</button>
        </form>
        <ResultsFocus focusKey={focusKey} headingId="report" level={3}>
          Report
        </ResultsFocus>
      </section>
    );
  }

  it('does not move focus on the first render or when the key is unchanged', () => {
    const { rerender } = render(<Page focusKey="a" />);
    expect(document.body).toHaveFocus();

    rerender(<Page focusKey="a" />);
    expect(document.body).toHaveFocus();
  });

  it('moves focus to the results heading when the key changes, and it follows the form', () => {
    const { rerender } = render(<Page focusKey="a" />);

    rerender(<Page focusKey="b" />);

    const heading = screen.getByRole('heading', { level: 3, name: 'Report' });
    expect(document.activeElement).toBe(heading);
    const form = screen.getByRole('form', { name: 'Choose a merchant' });
    expect(form.compareDocumentPosition(heading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('is an h2 by default', () => {
    render(<ResultsFocus focusKey="a">Results</ResultsFocus>);

    expect(screen.getByRole('heading', { level: 2, name: 'Results' })).toBeInTheDocument();
  });
});

describe('RetryClicksButton', () => {
  beforeEach(() => vi.clearAllMocks());

  it('refreshes the data and keeps focus on the page, at the report heading', async () => {
    const user = setupUser();
    render(
      <>
        <Region />
        <h3 id="report" tabIndex={-1}>
          Report
        </h3>
        <RetryClicksButton focusId="report" />
      </>
    );

    await user.click(screen.getByRole('button', { name: 'Retry' }));

    expect(mocks.refresh).toHaveBeenCalledTimes(1);
    expect(document.body).not.toHaveFocus();
    expect(document.activeElement).toBe(screen.getByRole('heading', { name: 'Report' }));
  });

  it('is a real button, not a link', () => {
    render(<RetryClicksButton focusId="report" />);

    expect(screen.getByRole('button', { name: 'Retry' })).toHaveAttribute('type', 'button');
    expect(screen.queryByRole('link')).toBeNull();
  });

  it('has a progress message for the status region', () => {
    expect(RETRY_PENDING_MESSAGE).toBe('Retrying the click report…');
  });
});
