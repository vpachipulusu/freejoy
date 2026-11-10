'use client';

import * as React from 'react';

import { cn } from '@/lib/utils';

// Below 640px the header is visually hidden and each row becomes a stacked item;
// cells show their `label` (data-label) in front of the value. Changing `display`
// on table parts can drop their table semantics in some browsers, so stacked
// tables carry explicit ARIA roles. The hidden header also hides sort buttons:
// stacked tables with SortableTableHead need a separate sort control on phones.
const STACKED_CLASSES =
  'max-sm:[&>thead]:sr-only max-sm:[&>tbody>tr]:flex max-sm:[&>tbody>tr]:flex-wrap max-sm:[&>tbody>tr]:items-baseline max-sm:[&>tbody>tr]:justify-between max-sm:[&>tbody>tr]:gap-x-4 max-sm:[&>tbody>tr]:py-3 max-sm:[&>tfoot>tr]:flex max-sm:[&>tfoot>tr]:flex-wrap max-sm:[&>tfoot>tr]:items-baseline max-sm:[&>tfoot>tr]:justify-between max-sm:[&>tfoot>tr]:gap-x-4 max-sm:[&>tfoot>tr]:py-3';

const StackedContext = React.createContext(false);

interface TableProps extends React.HTMLAttributes<HTMLTableElement> {
  /**
   * Opt in to stacked rows below 640px; cells need a `label` to name their column.
   * Do not combine with SortableTableHead: the header is visually hidden when
   * stacked, so the sort buttons become invisible while staying focusable.
   */
  stacked?: boolean;
}

const Table = React.forwardRef<HTMLTableElement, TableProps>(
  ({ className, stacked = false, ...props }, ref) => (
    <div className="relative w-full overflow-auto">
      <StackedContext.Provider value={stacked}>
        <table
          ref={ref}
          role={stacked ? 'table' : undefined}
          className={cn('w-full caption-bottom text-sm', stacked && STACKED_CLASSES, className)}
          {...props}
        />
      </StackedContext.Provider>
    </div>
  )
);
Table.displayName = 'Table';

const TableHeader = React.forwardRef<
  HTMLTableSectionElement,
  React.HTMLAttributes<HTMLTableSectionElement>
>(({ className, ...props }, ref) => (
  <thead
    ref={ref}
    role={React.useContext(StackedContext) ? 'rowgroup' : undefined}
    className={cn('[&_tr]:border-b', className)}
    {...props}
  />
));
TableHeader.displayName = 'TableHeader';

const TableBody = React.forwardRef<
  HTMLTableSectionElement,
  React.HTMLAttributes<HTMLTableSectionElement>
>(({ className, ...props }, ref) => (
  <tbody
    ref={ref}
    role={React.useContext(StackedContext) ? 'rowgroup' : undefined}
    className={cn('[&_tr:last-child]:border-0', className)}
    {...props}
  />
));
TableBody.displayName = 'TableBody';

const TableFooter = React.forwardRef<
  HTMLTableSectionElement,
  React.HTMLAttributes<HTMLTableSectionElement>
>(({ className, ...props }, ref) => (
  <tfoot
    ref={ref}
    role={React.useContext(StackedContext) ? 'rowgroup' : undefined}
    className={cn('border-t bg-muted/50 font-medium [&>tr]:last:border-b-0', className)}
    {...props}
  />
));
TableFooter.displayName = 'TableFooter';

const TableRow = React.forwardRef<HTMLTableRowElement, React.HTMLAttributes<HTMLTableRowElement>>(
  ({ className, ...props }, ref) => (
    <tr
      ref={ref}
      role={React.useContext(StackedContext) ? 'row' : undefined}
      className={cn(
        'border-b transition-colors hover:bg-secondary data-[state=selected]:bg-muted',
        className
      )}
      {...props}
    />
  )
);
TableRow.displayName = 'TableRow';

interface NumericProps {
  /** Right-align and use tabular figures (amounts, counts, dates). */
  numeric?: boolean;
}

const NUMERIC_CLASSES = 'text-right tabular-nums';

const TableHead = React.forwardRef<
  HTMLTableCellElement,
  React.ThHTMLAttributes<HTMLTableCellElement> & NumericProps
>(({ className, numeric = false, scope = 'col', ...props }, ref) => (
  <th
    ref={ref}
    scope={scope}
    role={React.useContext(StackedContext) ? 'columnheader' : undefined}
    className={cn(
      'h-11 bg-secondary px-4 text-left align-middle text-xs font-semibold uppercase tracking-wider text-muted-foreground [&:has([role=checkbox])]:pr-0 [&>[role=checkbox]]:translate-y-[2px]',
      numeric && NUMERIC_CLASSES,
      className
    )}
    {...props}
  />
));
TableHead.displayName = 'TableHead';

/** The `label` prefix a stacked cell shows before its value (below 640px). */
const LABELLED_CELL_CLASSES =
  'max-sm:p-0 max-sm:before:mr-2 max-sm:before:text-xs max-sm:before:font-semibold max-sm:before:uppercase max-sm:before:tracking-wider max-sm:before:text-muted-foreground max-sm:before:content-[attr(data-label)]';

interface TableCellProps extends React.TdHTMLAttributes<HTMLTableCellElement>, NumericProps {
  /** Column name shown before the value when rows are stacked (below 640px). */
  label?: string;
}

const TableCell = React.forwardRef<HTMLTableCellElement, TableCellProps>(
  ({ className, numeric = false, label, ...props }, ref) => (
    <td
      ref={ref}
      role={React.useContext(StackedContext) ? 'cell' : undefined}
      data-label={label}
      className={cn(
        'px-4 py-3 align-middle [&:has([role=checkbox])]:pr-0 [&>[role=checkbox]]:translate-y-[2px]',
        label && LABELLED_CELL_CLASSES,
        numeric && NUMERIC_CLASSES,
        className
      )}
      {...props}
    />
  )
);
TableCell.displayName = 'TableCell';

/** A row header cell (`th scope="row"`): the cell that names its row, laid out like a `TableCell`. */
const TableRowHeader = React.forwardRef<HTMLTableCellElement, TableCellProps>(
  ({ className, numeric = false, label, ...props }, ref) => (
    <th
      ref={ref}
      scope="row"
      role={React.useContext(StackedContext) ? 'rowheader' : undefined}
      data-label={label}
      className={cn(
        'px-4 py-3 text-left align-middle font-medium',
        label && LABELLED_CELL_CLASSES,
        numeric && NUMERIC_CLASSES,
        className
      )}
      {...(props as React.ThHTMLAttributes<HTMLTableCellElement>)}
    />
  )
);
TableRowHeader.displayName = 'TableRowHeader';

interface TableCaptionProps extends React.HTMLAttributes<HTMLTableCaptionElement> {
  /** Show the caption on screen; by default it is visually hidden. */
  visible?: boolean;
}

const TableCaption = React.forwardRef<HTMLTableCaptionElement, TableCaptionProps>(
  ({ className, visible = false, ...props }, ref) => (
    <caption
      ref={ref}
      className={cn(visible ? 'mt-4 text-sm text-muted-foreground' : 'sr-only', className)}
      {...props}
    />
  )
);
TableCaption.displayName = 'TableCaption';

type SortDirection = 'ascending' | 'descending' | 'none';

interface SortableTableHeadProps
  extends Omit<React.ThHTMLAttributes<HTMLTableCellElement>, 'onClick'>, NumericProps {
  sort: SortDirection;
  onSort: () => void;
}

const SORT_ICON: Record<SortDirection, string> = {
  ascending: '↑',
  descending: '↓',
  none: '↕',
};

const SortableTableHead = React.forwardRef<HTMLTableCellElement, SortableTableHeadProps>(
  ({ sort, onSort, numeric = false, children, className, ...props }, ref) => (
    <TableHead
      ref={ref}
      aria-sort={sort}
      numeric={numeric}
      className={cn('p-0', className)}
      {...props}
    >
      <button
        type="button"
        onClick={onSort}
        className={cn(
          'inline-flex min-h-11 w-full items-center gap-1 px-4 text-xs font-semibold uppercase tracking-wider hover:text-foreground',
          numeric ? 'justify-end' : 'justify-start'
        )}
      >
        {children}
        <span aria-hidden="true">{SORT_ICON[sort]}</span>
      </button>
    </TableHead>
  )
);
SortableTableHead.displayName = 'SortableTableHead';

export {
  SortableTableHead,
  Table,
  TableHeader,
  TableBody,
  TableFooter,
  TableHead,
  TableRow,
  TableCell,
  TableRowHeader,
  TableCaption,
};
