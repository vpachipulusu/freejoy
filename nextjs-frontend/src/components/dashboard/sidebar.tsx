'use client';

import type { ReactNode } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { siteConfig } from '@/config/site';
import { cn } from '@/lib/utils';
import { ArrowLeft } from 'lucide-react';
import { NAV_ITEMS, SITE_HOME_HREF, activeNavHref, backToSiteLabel } from './nav-items';

/**
 * Desktop navigation: a 240px inverse panel from 900px up. Below that the TabBar takes over.
 * Text uses background/foreground tokens so it stays light on --feature in both colour modes.
 * `children` is an optional slot rendered between the brand and the main nav.
 */
export function Sidebar({ children }: { children?: ReactNode }) {
  const current = activeNavHref(usePathname());

  return (
    <aside
      aria-label="Workspace"
      className="on-inverse fixed inset-y-0 start-0 z-40 flex w-60 flex-col gap-6 bg-feature px-3 py-4 text-background dark:text-foreground max-[899px]:hidden"
    >
      <Link
        href="/dashboard"
        className="flex min-h-11 items-center rounded-md px-3 font-display text-xl font-semibold"
      >
        {siteConfig.name}
      </Link>
      {children}
      <nav aria-label="Main">
        <ul className="flex flex-col gap-1">
          {NAV_ITEMS.map(({ href, label, icon: Icon }) => {
            const isCurrent = href === current;
            return (
              <li key={href}>
                <Link
                  href={href}
                  aria-current={isCurrent ? 'page' : undefined}
                  className={cn(
                    'relative flex min-h-11 items-center gap-3 rounded-md px-3 text-sm font-medium transition-colors',
                    'text-background/75 hover:bg-background/10 hover:text-background',
                    'dark:text-foreground/75 dark:hover:bg-foreground/10 dark:hover:text-foreground',
                    isCurrent &&
                      'bg-background/15 text-background before:absolute before:inset-y-1 before:start-0 before:w-[3px] before:rounded-full before:bg-cta dark:bg-foreground/15 dark:text-foreground'
                  )}
                >
                  <Icon className="h-[18px] w-[18px] shrink-0" aria-hidden="true" />
                  {label}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>
      <Link
        href={SITE_HOME_HREF}
        className="mt-auto flex min-h-11 items-center gap-3 rounded-md px-3 text-sm font-medium text-background/75 transition-colors hover:bg-background/10 hover:text-background dark:text-foreground/75 dark:hover:bg-foreground/10 dark:hover:text-foreground"
      >
        <ArrowLeft className="h-[18px] w-[18px] shrink-0" aria-hidden="true" />
        {backToSiteLabel(siteConfig.name)}
      </Link>
    </aside>
  );
}
