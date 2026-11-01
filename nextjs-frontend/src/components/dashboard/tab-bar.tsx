'use client';

import { useState, useSyncExternalStore } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import * as DialogPrimitive from '@radix-ui/react-dialog';
import { ArrowLeft, MoreHorizontal, X } from 'lucide-react';
import { DialogOverlay } from '@/components/ui/dialog';
import { siteConfig } from '@/config/site';
import { cn } from '@/lib/utils';
import { MORE_ITEMS, SITE_HOME_HREF, TAB_ITEMS, activeNavHref, backToSiteLabel } from './nav-items';

const TAB_CLASS =
  'relative flex h-full flex-1 flex-col items-center justify-center gap-0.5 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground';
const TAB_CURRENT_CLASS =
  'text-foreground before:absolute before:inset-x-3 before:top-0 before:h-[3px] before:rounded-b-full before:bg-cta';

const WIDE_QUERY = '(min-width: 900px)';

function subscribeWide(onChange: () => void): () => void {
  const query = window.matchMedia(WIDE_QUERY);
  query.addEventListener('change', onChange);
  return () => query.removeEventListener('change', onChange);
}

function readWide(): boolean {
  return window.matchMedia(WIDE_QUERY).matches;
}

/**
 * Phone navigation: a 56px bottom tab bar below 900px. Items past the third open in a bottom sheet built on
 * the Radix dialog, which traps focus and returns it to the More button when it closes.
 */
export function TabBar() {
  const pathname = usePathname();
  const current = activeNavHref(pathname);
  // The sheet belongs to the page it was opened on and to phone widths. Once either stops being true the stored
  // state is cleared (during render, not in an effect), so Back or Forward and a resize round trip cannot bring a
  // stale sheet back.
  const [openedOn, setOpenedOn] = useState<string | null>(null);
  const isWide = useSyncExternalStore(subscribeWide, readWide, () => false);
  if (openedOn !== null && (openedOn !== pathname || isWide)) setOpenedOn(null);
  const moreOpen = openedOn === pathname;
  const setMoreOpen = (open: boolean) => setOpenedOn(open ? pathname : null);
  const moreIsCurrent = MORE_ITEMS.some((item) => item.href === current);

  return (
    <nav
      aria-label="Main"
      className="fixed inset-x-0 bottom-0 z-40 box-content flex h-14 border-t border-border bg-background pb-[env(safe-area-inset-bottom)] min-[900px]:hidden"
    >
      {TAB_ITEMS.map(({ href, label, icon: Icon }) => {
        const isCurrent = href === current;
        return (
          <Link
            key={href}
            href={href}
            aria-current={isCurrent ? 'page' : undefined}
            className={cn(TAB_CLASS, isCurrent && TAB_CURRENT_CLASS)}
          >
            <Icon className="h-5 w-5" aria-hidden="true" />
            {label}
          </Link>
        );
      })}

      <DialogPrimitive.Root open={moreOpen} onOpenChange={setMoreOpen}>
        <DialogPrimitive.Trigger
          data-current={moreIsCurrent ? 'true' : undefined}
          className={cn(TAB_CLASS, moreIsCurrent && TAB_CURRENT_CLASS)}
        >
          <MoreHorizontal className="h-5 w-5" aria-hidden="true" />
          More
          {moreIsCurrent && <span className="sr-only">, current section</span>}
        </DialogPrimitive.Trigger>
        <DialogPrimitive.Portal>
          <DialogOverlay />
          <DialogPrimitive.Content
            aria-describedby={undefined}
            className="fixed inset-x-0 bottom-0 z-50 rounded-t-lg border-t border-border bg-background p-4 pb-[calc(1rem+env(safe-area-inset-bottom))] shadow-lg data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:slide-out-to-bottom data-[state=open]:slide-in-from-bottom"
          >
            <div className="flex items-center justify-between">
              <DialogPrimitive.Title className="font-display text-lg font-semibold">
                More
              </DialogPrimitive.Title>
              <DialogPrimitive.Close
                aria-label="Close"
                className="flex h-11 w-11 items-center justify-center rounded-md hover:bg-secondary"
              >
                <X className="h-5 w-5" aria-hidden="true" />
              </DialogPrimitive.Close>
            </div>
            <ul className="mt-2 flex flex-col">
              {MORE_ITEMS.map(({ href, label, icon: Icon }) => (
                <li key={href}>
                  <Link
                    href={href}
                    aria-current={href === current ? 'page' : undefined}
                    onClick={() => setMoreOpen(false)}
                    className={cn(
                      'flex min-h-11 items-center gap-3 rounded-md px-2 text-sm font-medium hover:bg-secondary',
                      href === current &&
                        'relative bg-secondary before:absolute before:inset-y-2 before:start-0 before:w-[3px] before:rounded-full before:bg-cta'
                    )}
                  >
                    <Icon className="h-[18px] w-[18px]" aria-hidden="true" />
                    {label}
                  </Link>
                </li>
              ))}
              <li className="mt-1 border-t border-border pt-1">
                <Link
                  href={SITE_HOME_HREF}
                  onClick={() => setMoreOpen(false)}
                  className="flex min-h-11 items-center gap-3 rounded-md px-2 text-sm font-medium hover:bg-secondary"
                >
                  <ArrowLeft className="h-[18px] w-[18px]" aria-hidden="true" />
                  {backToSiteLabel(siteConfig.name)}
                </Link>
              </li>
            </ul>
          </DialogPrimitive.Content>
        </DialogPrimitive.Portal>
      </DialogPrimitive.Root>
    </nav>
  );
}
