'use client';

import { useSyncExternalStore } from 'react';
import { getSavedSnapshot, getServerSavedSnapshot, subscribeSaved } from '@/lib/saved/saved-store';

/** The saved slugs, oldest first. Empty on the server and during hydration, then the browser's list. */
export function useSavedSlugs(): readonly string[] {
  return useSyncExternalStore(subscribeSaved, getSavedSnapshot, getServerSavedSnapshot);
}

/** Whether one offer is saved. A boolean snapshot, so a heart re-renders only when its own offer changes. */
export function useIsSaved(slug: string): boolean {
  return useSyncExternalStore(
    subscribeSaved,
    () => getSavedSnapshot().includes(slug),
    () => false
  );
}

const subscribeNever = () => () => {};

/** False on the server and during hydration, true afterwards: tells "not read yet" from "nothing saved". */
export function useHydrated(): boolean {
  return useSyncExternalStore(
    subscribeNever,
    () => true,
    () => false
  );
}
