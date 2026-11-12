import type { ComponentProps } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { recordNavigationAnnouncements } from '@/__tests__/unit/helpers/navigation-announcements';

// next/form needs the app router; the stand-in renders a plain form and, like the real one, cancels the submit.
vi.mock('next/form', () => ({
  default: ({
    action,
    onSubmit,
    ...props
  }: ComponentProps<'form'> & { action: string; scroll?: boolean }) => (
    <form
      action={action}
      {...props}
      onSubmit={(event) => {
        onSubmit?.(event);
        event.preventDefault();
      }}
    />
  ),
}));

import { AnnouncingForm } from '@/components/site/announcing-form';

describe('AnnouncingForm', () => {
  it('announces the action with the fields when it submits, so the indicator shows for a next/form', () => {
    const announced = recordNavigationAnnouncements();
    render(
      <AnnouncingForm action="/admin/audit-logs" aria-label="Filter">
        <input name="action" defaultValue="login" />
      </AnnouncingForm>
    );

    fireEvent.submit(screen.getByRole('form', { name: 'Filter' }));

    expect(announced.hrefs).toEqual(['http://localhost:3000/admin/audit-logs?action=login']);
  });

  it('announces nothing for a submit that stays on the page that is open', () => {
    window.history.pushState({}, '', '/admin/audit-logs?action=login');
    const announced = recordNavigationAnnouncements();
    render(
      <AnnouncingForm action="/admin/audit-logs" aria-label="Filter">
        <input name="action" defaultValue="login" />
      </AnnouncingForm>
    );

    fireEvent.submit(screen.getByRole('form', { name: 'Filter' }));

    expect(announced.hrefs).toEqual([]);
    window.history.pushState({}, '', '/');
  });
});
