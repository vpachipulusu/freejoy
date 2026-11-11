import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { setupUser } from '@/__tests__/unit/helpers/user';
import { CataloguePagination, pageWindow } from '@/components/catalogue/catalogue-pagination';
import { FilterDrawer } from '@/components/catalogue/filter-drawer';
import { SortSelect } from '@/components/catalogue/sort-select';
import { DEFAULT_QUERY, SORT_LABELS, parseCatalogueParams } from '@/lib/catalogue';

describe('filter drawer', () => {
  it('opens from the Filter button and closes with Escape', async () => {
    const user = setupUser();
    render(
      <FilterDrawer>
        {/* eslint-disable-next-line @next/next/no-html-link-for-pages -- stand-in for a filter link */}
        <a href="/freebies?type=cashback">Cashback</a>
      </FilterDrawer>
    );
    expect(screen.queryByRole('dialog')).toBeNull();

    await user.click(screen.getByRole('button', { name: 'Filter' }));

    expect(screen.getByRole('dialog', { name: 'Filter freebies' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Cashback' })).toBeInTheDocument();

    await user.keyboard('{Escape}');

    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.getByRole('button', { name: 'Filter' })).toHaveFocus();
  });

  it('closes when a filter link inside it is chosen', async () => {
    const user = setupUser();
    render(
      <FilterDrawer>
        {/* eslint-disable-next-line @next/next/no-html-link-for-pages -- stand-in for a filter link */}
        <a href="/freebies?type=cashback" onClick={(event) => event.preventDefault()}>
          Cashback
        </a>
      </FilterDrawer>
    );
    await user.click(screen.getByRole('button', { name: 'Filter' }));

    await user.click(screen.getByRole('link', { name: 'Cashback' }));

    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('shows how many drawer filters are active', () => {
    render(
      <FilterDrawer activeCount={2}>
        <span />
      </FilterDrawer>
    );

    expect(screen.getByRole('button', { name: /Filter\s*2 active/ })).toBeInTheDocument();
  });
});

describe('sort select', () => {
  it('offers every sort option and selects the one in the URL', () => {
    render(<SortSelect query={parseCatalogueParams({ sort: 'a_z' })} />);

    const select = screen.getByLabelText('Sort by');
    const labels = screen.getAllByRole('option').map((option) => option.textContent);
    expect(labels).toEqual(Object.values(SORT_LABELS));
    expect(select).toHaveValue('a_z');
    expect(select).toHaveAttribute('name', 'sort');
  });

  it('carries the search and filters as hidden inputs but drops the page', () => {
    const query = parseCatalogueParams({
      q: 'tea',
      category: 'food-drink',
      type: 'free_sample,voucher',
      postage: 'free',
      page: '2',
    });

    const { container } = render(<SortSelect query={query} />);

    const hidden = [...container.querySelectorAll<HTMLInputElement>('input[type="hidden"]')].map(
      (input) => [input.name, input.value]
    );
    expect(hidden).toEqual([
      ['q', 'tea'],
      ['category', 'food-drink'],
      ['type', 'free_sample,voucher'],
      ['postage', 'free'],
    ]);
  });

  it('submits to /freebies with no hidden inputs when nothing is filtered, and keeps an Apply button for no-JS', () => {
    const { container } = render(<SortSelect query={DEFAULT_QUERY} />);

    const form = container.querySelector('form');
    expect(form).toHaveAttribute('action', '/freebies');
    expect(form).toHaveAttribute('method', 'get');
    expect(container.querySelectorAll('input[type="hidden"]')).toHaveLength(0);
    expect(screen.getByRole('button', { name: 'Apply' })).toBeInTheDocument();
  });

  it('stays on the category page and leaves the category out of the form fields', () => {
    const query = { ...DEFAULT_QUERY, category: 'pets' };

    const { container } = render(<SortSelect query={query} scopeCategory="pets" />);

    expect(container.querySelector('form')).toHaveAttribute('action', '/category/pets');
    expect(container.querySelector('input[name="category"]')).toBeNull();
  });
});

describe('catalogue pagination', () => {
  const range = (page: number, pageCount: number, total: number) => ({
    page,
    pageCount,
    total,
    from: (page - 1) * 12 + 1,
    to: Math.min(page * 12, total),
  });

  it('disables Previous on the first page and links Next to page 2', () => {
    render(<CataloguePagination page={range(1, 3, 30)} query={DEFAULT_QUERY} />);

    expect(screen.queryByRole('link', { name: /Previous/ })).toBeNull();
    expect(screen.getByText('Previous').closest('[aria-disabled="true"]')).not.toBeNull();
    expect(screen.getByRole('link', { name: /Next/ })).toHaveAttribute('href', '/freebies?page=2');
    expect(screen.getByText('Showing 1–12 of 30')).toBeInTheDocument();
  });

  it('disables Next on the last page and links Previous to the page before', () => {
    render(<CataloguePagination page={range(3, 3, 30)} query={DEFAULT_QUERY} />);

    expect(screen.queryByRole('link', { name: /Next/ })).toBeNull();
    expect(screen.getByText('Next').closest('[aria-disabled="true"]')).not.toBeNull();
    expect(screen.getByRole('link', { name: /Previous/ })).toHaveAttribute(
      'href',
      '/freebies?page=2'
    );
    expect(screen.getByText('Showing 25–30 of 30')).toBeInTheDocument();
  });

  it('keeps every active param on the page links and marks the current page', () => {
    const query = parseCatalogueParams({ q: 'pet', type: 'cashback', sort: 'a_z', page: '2' });

    render(<CataloguePagination page={range(2, 3, 30)} query={query} />);

    expect(screen.getByRole('link', { name: 'Page 3' })).toHaveAttribute(
      'href',
      '/freebies?q=pet&type=cashback&sort=a_z&page=3'
    );
    expect(screen.getByRole('link', { name: 'Page 1' })).toHaveAttribute(
      'href',
      '/freebies?q=pet&type=cashback&sort=a_z'
    );
    expect(screen.getByRole('link', { name: 'Page 2' })).toHaveAttribute('aria-current', 'page');
  });

  it('uses the category path for its links on a category page', () => {
    const query = { ...DEFAULT_QUERY, category: 'pets' };

    render(<CataloguePagination page={range(1, 2, 20)} query={query} scopeCategory="pets" />);

    expect(screen.getByRole('link', { name: /Next/ })).toHaveAttribute(
      'href',
      '/category/pets?page=2'
    );
  });

  it('shows only the range when everything fits on one page', () => {
    render(<CataloguePagination page={range(1, 1, 5)} query={DEFAULT_QUERY} />);

    expect(screen.getByText('Showing 1–5 of 5')).toBeInTheDocument();
    expect(screen.queryByRole('link')).toBeNull();
  });

  it('windows long page lists with gaps', () => {
    expect(pageWindow(1, 3)).toEqual([1, 2, 3]);
    expect(pageWindow(5, 9)).toEqual([1, null, 4, 5, 6, null, 9]);
  });
});
