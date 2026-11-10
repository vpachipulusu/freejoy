/**
 * The audit log filter form, pager and results focus: a GET form (every filter in the URL), labelled and grouped
 * controls, 44px targets, notices that mark rejected fields invalid, a Reset link, a pager that keeps the filters
 * and fits a phone, and focus that lands on the results heading instead of falling to the body.
 */
import { describe, expect, it } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { AuditLogsFilters } from '@/app/(admin)/admin/audit-logs/audit-logs-filters';
import { AuditLogsPagination } from '@/app/(admin)/admin/audit-logs/audit-logs-pagination';
import {
  auditLogsHref,
  parseAuditLogsQuery,
  type AuditLogsQuery,
} from '@/app/(admin)/admin/audit-logs/audit-logs-query';
import { ResultsFocus } from '@/app/(admin)/admin/_components/results-focus';

type Raw = Record<string, string>;
const parsedOf = (raw: Raw = {}) => parseAuditLogsQuery(raw);
const queryOf = (raw: Raw = {}): AuditLogsQuery => parsedOf(raw).query;
const filters = (raw: Raw = {}) => <AuditLogsFilters {...parsedOf(raw)} />;

describe('AuditLogsFilters', () => {
  it('is a search form to the audit log path that submits with GET, so filters live in the URL', () => {
    render(filters());

    const form = screen.getByRole('search', { name: 'Filter audit log entries' });
    expect(form).toHaveAttribute('action', '/admin/audit-logs');
    expect(form.getAttribute('method')?.toLowerCase() ?? 'get').toBe('get');
  });

  it('labels every control and names the parameter each one submits', () => {
    render(filters());

    expect(screen.getByLabelText('Action')).toHaveAttribute('name', 'action');
    expect(screen.getByLabelText('User ID')).toHaveAttribute('name', 'user');
    expect(screen.getByLabelText('Organisation ID')).toHaveAttribute('name', 'org');
    expect(screen.getByLabelText('From date')).toHaveAttribute('type', 'date');
    expect(screen.getByLabelText('From date')).toHaveAttribute('name', 'from');
    expect(screen.getByLabelText('To date')).toHaveAttribute('name', 'to');
    expect(screen.getByLabelText('Entries per page')).toHaveAttribute('name', 'size');
  });

  it('never submits a page number, so applying filters starts at page 1', () => {
    const { container } = render(filters({ page: '4' }));

    expect(container.querySelector('[name="page"]')).toBeNull();
  });

  it('describes every text field and the dates for assistive technology', () => {
    render(filters());

    expect(screen.getByLabelText('Action')).toHaveAccessibleDescription(
      'Exact action, for example admin.offers.update'
    );
    expect(screen.getByLabelText('User ID')).toHaveAccessibleDescription(
      'The admin who made the change'
    );
    expect(screen.getByLabelText('Organisation ID')).toHaveAccessibleDescription(
      'The organisation the entry is about'
    );
    expect(screen.getByLabelText('From date')).toHaveAccessibleDescription(
      'Dates are in UTC and include the whole day.'
    );
    expect(screen.getByLabelText('To date')).toHaveAccessibleDescription(
      'Dates are in UTC and include the whole day.'
    );
  });

  it('groups the two dates under one labelled date range', () => {
    render(filters());

    const group = screen.getByRole('group', { name: /date range/i });
    expect(within(group).getByLabelText('From date')).toBeInTheDocument();
    expect(within(group).getByLabelText('To date')).toBeInTheDocument();
    expect(group).toHaveAccessibleName('Date range (UTC, whole days)');
  });

  it('stops the date pickers offering an inverted range', () => {
    render(filters({ from: '2026-01-05', to: '2026-01-09' }));

    expect(screen.getByLabelText('From date')).toHaveAttribute('max', '2026-01-09');
    expect(screen.getByLabelText('To date')).toHaveAttribute('min', '2026-01-05');
  });

  it('offers the allow-listed page sizes and selects the current one', () => {
    render(filters({ size: '25' }));

    const options = screen.getAllByRole('option').map((option) => option.textContent);
    expect(options).toEqual(['25', '50', '100']);
    expect(screen.getByLabelText('Entries per page')).toHaveValue('25');
  });

  it('shows the current filters in the controls', () => {
    render(
      filters({
        action: 'admin.users.ban',
        user: 'u1',
        org: 'o1',
        from: '2026-01-01',
        to: '2026-01-31',
      })
    );

    expect(screen.getByLabelText('Action')).toHaveValue('admin.users.ban');
    expect(screen.getByLabelText('User ID')).toHaveValue('u1');
    expect(screen.getByLabelText('Organisation ID')).toHaveValue('o1');
    expect(screen.getByLabelText('From date')).toHaveValue('2026-01-01');
    expect(screen.getByLabelText('To date')).toHaveValue('2026-01-31');
  });

  it('bounds the text inputs like the API does', () => {
    render(filters());

    expect(screen.getByLabelText('Action')).toHaveAttribute('maxlength', '100');
    expect(screen.getByLabelText('User ID')).toHaveAttribute('maxlength', '128');
    expect(screen.getByLabelText('Organisation ID')).toHaveAttribute('maxlength', '128');
  });

  it('has an Apply button and a Reset link back to the bare path', () => {
    render(filters({ action: 'admin.users.ban' }));

    expect(screen.getByRole('button', { name: 'Apply filters' })).toHaveAttribute('type', 'submit');
    expect(screen.getByRole('link', { name: 'Reset filters' })).toHaveAttribute(
      'href',
      '/admin/audit-logs'
    );
  });

  it('keeps every control at least 44px tall', () => {
    render(filters());

    for (const control of [
      screen.getByLabelText('Action'),
      screen.getByLabelText('From date'),
      screen.getByLabelText('Entries per page'),
      screen.getByRole('button', { name: 'Apply filters' }),
      screen.getByRole('link', { name: 'Reset filters' }),
    ]) {
      expect(control.className).toMatch(/\bh-11\b|\bmin-h-11\b/);
    }
  });

  it('wraps the fields into a responsive grid and the buttons into a wrapping row', () => {
    const { container } = render(filters());

    expect(container.querySelector('.grid')?.className).toContain('sm:grid-cols-2');
    expect(
      screen.getByRole('button', { name: 'Apply filters' }).parentElement?.className
    ).toContain('flex-wrap');
  });

  describe('notices and invalid fields', () => {
    it('always renders the status container, empty when there are no notices', () => {
      render(filters());

      const status = screen.getByRole('status');
      expect(status).toHaveAttribute('id', 'audit-filter-notices');
      expect(status).toBeEmptyDOMElement();
      expect(screen.queryByRole('list')).not.toBeInTheDocument();
    });

    it('puts the notices in a real list inside the status container', () => {
      render(filters({ action: 'Bad Value', user: 'no good!' }));

      const status = screen.getByRole('status');
      const list = within(status).getByRole('list');
      expect(within(list).getAllByRole('listitem')).toHaveLength(2);
      expect(status).toHaveTextContent('The action filter was ignored');
      expect(status).toHaveTextContent('The user ID filter was ignored');
    });

    it('marks a rejected field invalid and describes it with its notice, without echoing the value', () => {
      render(filters({ action: 'Bad Value' }));

      const action = screen.getByLabelText('Action');
      expect(action).toHaveAttribute('aria-invalid', 'true');
      expect(action).toHaveValue('');
      expect(action).toHaveAccessibleDescription(
        /Exact action, for example admin\.offers\.update\s+The action filter was ignored/
      );
      expect(document.body.innerHTML).not.toContain('Bad Value');
      expect(screen.getByLabelText('User ID')).not.toHaveAttribute('aria-invalid');
    });

    it.each([
      ['user', 'User ID', 'The user ID filter was ignored'],
      ['org', 'Organisation ID', 'The organisation ID filter was ignored'],
      ['from', 'From date', 'The "From" date was ignored'],
      ['to', 'To date', 'The "To" date was ignored'],
    ])('flags a rejected %s value on %s', (key, label, notice) => {
      render(filters({ [key]: key === 'from' || key === 'to' ? '2026-02-30' : 'not valid!' }));

      const field = screen.getByLabelText(label);
      expect(field).toHaveAttribute('aria-invalid', 'true');
      expect(field).toHaveAccessibleDescription(new RegExp(notice));
    });

    it('flags both dates, described by the range notice, when To is before From', () => {
      render(filters({ from: '2026-03-02', to: '2026-03-01' }));

      for (const label of ['From date', 'To date']) {
        const field = screen.getByLabelText(label);
        expect(field).toHaveAttribute('aria-invalid', 'true');
        expect(field).toHaveAccessibleDescription(/"To" is before "From"/);
      }
    });

    it('does not mark accepted fields invalid', () => {
      render(filters({ action: 'admin.users.ban', from: '2026-01-01' }));

      expect(document.querySelector('[aria-invalid]')).toBeNull();
    });
  });
});

describe('AuditLogsPagination', () => {
  const pager = (raw: Raw, total: number) => (
    <AuditLogsPagination query={queryOf(raw)} total={total} />
  );

  it('renders nothing for a single page', () => {
    const { container } = render(pager({}, 50));

    expect(container).toBeEmptyDOMElement();
  });

  it('links Next with the filters kept', () => {
    render(pager({ action: 'admin.users.ban', size: '25' }, 80));

    expect(screen.getByRole('link', { name: 'Next' })).toHaveAttribute(
      'href',
      '/admin/audit-logs?action=admin.users.ban&size=25&page=2'
    );
    expect(screen.getByText('Page 1 of 4')).toBeInTheDocument();
  });

  it('links Previous back to page 1 without a page parameter', () => {
    render(pager({ page: '2', user: 'u1' }, 120));

    expect(screen.getByRole('link', { name: 'Previous' })).toHaveAttribute(
      'href',
      '/admin/audit-logs?user=u1'
    );
  });

  it('marks the links as previous and next for the browser', () => {
    render(pager({ page: '2' }, 200));

    expect(screen.getByRole('link', { name: 'Previous' })).toHaveAttribute('rel', 'prev');
    expect(screen.getByRole('link', { name: 'Next' })).toHaveAttribute('rel', 'next');
  });

  it('keeps a page indicator for a page past the data', () => {
    render(pager({ page: '3' }, 10));

    expect(screen.getByText('Page 3 of 3')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Next' })).not.toBeInTheDocument();
  });

  it('wraps on a phone: the indicator takes its own row above the buttons, then sits between them', () => {
    render(pager({ page: '2' }, 200));

    const nav = screen.getByRole('navigation', { name: 'Pagination' });
    expect(nav.className).toContain('flex-wrap');
    const indicator = screen.getByText('Page 2 of 4');
    expect(indicator.className).toContain('order-first');
    expect(indicator.className).toContain('w-full');
    expect(indicator.className).toContain('sm:order-none');
    expect(indicator.className).toContain('sm:w-auto');
    expect(screen.getByRole('link', { name: 'Next' }).className).toContain('h-11');
  });

  describe('the offset cap', () => {
    it('says "of N+" and shows the narrow-the-filters note on every page when results run past it', () => {
      render(pager({}, 50_000));

      expect(screen.getByText('Page 1 of 201+')).toBeInTheDocument();
      expect(screen.getByText(/Narrow the filters or the date range/)).toBeInTheDocument();
      expect(screen.getByRole('link', { name: 'Next' })).toBeInTheDocument();
    });

    it('offers no Next on the last reachable page of a capped result', () => {
      render(pager({ page: '201' }, 50_000));

      expect(screen.getByText('Page 201 of 201+')).toBeInTheDocument();
      expect(screen.queryByRole('link', { name: 'Next' })).not.toBeInTheDocument();
      expect(screen.getByText(/Narrow the filters or the date range/)).toBeInTheDocument();
    });

    it('treats a total that ends exactly on the last reachable page as complete', () => {
      render(pager({ page: '201' }, 10_050));

      expect(screen.getByText('Page 201 of 201')).toBeInTheDocument();
      expect(screen.queryByRole('link', { name: 'Next' })).not.toBeInTheDocument();
      expect(screen.queryByText(/Narrow the filters/)).not.toBeInTheDocument();
    });

    it('shows the note as soon as one more entry cannot be reached', () => {
      render(pager({ page: '201' }, 10_051));

      expect(screen.getByText('Page 201 of 201+')).toBeInTheDocument();
      expect(screen.getByText(/Narrow the filters or the date range/)).toBeInTheDocument();
    });
  });
});

describe('ResultsFocus', () => {
  function Results({ raw, total }: { raw: Raw; total: number }) {
    const query = queryOf(raw);
    return (
      <>
        <ResultsFocus focusKey={auditLogsHref(query)}>Recent Activity</ResultsFocus>
        <AuditLogsPagination query={query} total={total} />
      </>
    );
  }

  it('renders the results heading as a programmatically focusable h2 that uses the global focus outline', () => {
    render(<Results raw={{}} total={120} />);

    const heading = screen.getByRole('heading', { level: 2, name: 'Recent Activity' });
    expect(heading).toHaveAttribute('tabindex', '-1');
    expect(heading.className).not.toMatch(/outline|ring/);
  });

  it('does not take focus when the page first renders', () => {
    render(<Results raw={{}} total={120} />);

    expect(document.activeElement).toBe(document.body);
  });

  it('moves focus to the heading when Next disappears on the last page, instead of dropping it on the body', () => {
    const { rerender } = render(<Results raw={{ page: '2' }} total={150} />);
    screen.getByRole('link', { name: 'Next' }).focus();
    expect(document.activeElement).toBe(screen.getByRole('link', { name: 'Next' }));

    rerender(<Results raw={{ page: '3' }} total={150} />);

    expect(screen.queryByRole('link', { name: 'Next' })).not.toBeInTheDocument();
    expect(document.activeElement).not.toBe(document.body);
    expect(document.activeElement).toBe(screen.getByRole('heading', { name: 'Recent Activity' }));
  });

  it('moves focus to the heading when Previous disappears on the first page', () => {
    const { rerender } = render(<Results raw={{ page: '2' }} total={150} />);
    screen.getByRole('link', { name: 'Previous' }).focus();

    rerender(<Results raw={{}} total={150} />);

    expect(screen.queryByRole('link', { name: 'Previous' })).not.toBeInTheDocument();
    expect(document.activeElement).toBe(screen.getByRole('heading', { name: 'Recent Activity' }));
  });

  it('leaves focus alone when a re-render does not change the results', () => {
    const { rerender } = render(<Results raw={{ page: '2' }} total={150} />);
    const next = screen.getByRole('link', { name: 'Next' });
    next.focus();

    rerender(<Results raw={{ page: '2' }} total={150} />);

    expect(document.activeElement).toBe(next);
  });
});
