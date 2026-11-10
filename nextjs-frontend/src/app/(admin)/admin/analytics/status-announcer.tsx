'use client';

import { useSyncExternalStore } from 'react';
import { createPortal } from 'react-dom';

/** The id of the one status region of the clicks section (rendered once, outside the streamed report). */
export const CLICKS_STATUS_ID = 'clicks-status';

const subscribe = () => () => undefined;
const findRegion = () => document.getElementById(CLICKS_STATUS_ID);
const noRegionOnServer = () => null;

/**
 * Writes `message` into the section's persistent status region. A live region is only announced when its text
 * changes after it is in the page, so the region is rendered once with the section and the streamed pieces (the
 * loading line, the report summary, a retry) put their text into that same element through a portal. The
 * announcement happens in the browser after hydration, so nothing is announced twice and a page without script
 * simply has no announcement.
 */
export function StatusAnnouncer({ message }: { message: string }) {
  // The server (and the hydration pass) has no region to write to; the browser reads it from the page.
  const target = useSyncExternalStore(subscribe, findRegion, noRegionOnServer);
  return target ? createPortal(message, target) : null;
}
