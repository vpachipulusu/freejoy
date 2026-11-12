'use client';

import type { ComponentProps } from 'react';
import Form from 'next/form';

import { announceFormNavigation } from './navigation-events';

/**
 * `next/form` `<Form>` that tells the global navigation indicator when it submits. `<Form>` cancels the native
 * submit and calls `router.push` itself, so the indicator cannot see it any other way. A client component so the
 * admin server pages can use it.
 */
export function AnnouncingForm(props: Omit<ComponentProps<typeof Form>, 'onSubmit'>) {
  return <Form {...props} onSubmit={announceFormNavigation} />;
}
