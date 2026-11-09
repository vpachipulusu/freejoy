'use client';

import { useState } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import * as DialogPrimitive from '@radix-ui/react-dialog';
import { Heart, Menu, Search, X } from 'lucide-react';
import { DialogOverlay } from '@/components/ui/dialog';
import { cn } from '@/lib/utils';
import { BrandMark } from './brand-mark';
import { SAVED_NAV_ITEM, SITE_NAV_ITEMS } from './nav-items';
import { SearchForm } from './search-form';
import { useSignedIn } from './use-signed-in';

export const PILL_CTA_CLASS =
  'inline-flex min-h-11 shrink-0 items-center justify-center whitespace-nowrap rounded-pill bg-cta px-5 text-sm font-bold text-cta-foreground shadow-sm transition-colors hover:bg-cta-hover';
const ICON_BUTTON =
  'flex h-11 w-11 items-center justify-center rounded-pill border border-border bg-card text-foreground transition-colors hover:bg-muted';

function isCurrentNav(href: string, pathname: string | null): boolean {
  return pathname === href && !href.includes('#') && !href.includes('?');
}

/**
 * Public header (design screens 01-04 desktop, 05-07 mobile). Desktop: wordmark, nav, search pill,
 * Log in, coral alerts pill. Below 1024px: wordmark, a search toggle and a menu sheet (Radix
 * dialog, focus-trapped). Sign-in is optional everywhere: "Log in" is a plain link to /login. The markup is
 * session-blind (static pages); after hydration `useSignedIn` swaps "Log in" for a "Dashboard" link when a
 * session cookie exists. Any failure leaves "Log in".
 */
export function SiteHeader() {
  const pathname = usePathname();
  const [menuOpen, setMenuOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const signedIn = useSignedIn();
  const account = signedIn
    ? { href: '/dashboard', label: 'Dashboard', prefetch: false }
    : { href: '/login', label: 'Log in', prefetch: undefined };

  return (
    <header className="sticky top-0 z-40 border-b border-border bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/80">
      <div className="mx-auto flex h-[72px] max-w-content items-center gap-4 px-4 sm:px-6">
        <BrandMark />

        <nav aria-label="Main" className="hidden lg:block">
          <ul className="flex items-center gap-1">
            {SITE_NAV_ITEMS.map(({ href, label, wideOnly }) => {
              const current = isCurrentNav(href, pathname);
              return (
                <li key={href} className={cn(wideOnly && 'hidden min-[1200px]:list-item')}>
                  <Link
                    href={href}
                    aria-current={current ? 'page' : undefined}
                    className={cn(
                      'inline-flex min-h-11 items-center rounded-pill px-3.5 text-sm font-semibold text-foreground transition-colors hover:bg-muted',
                      current && 'bg-accent text-accent-ink'
                    )}
                  >
                    {label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>

        <div className="ml-auto hidden flex-1 items-center justify-end gap-3 lg:flex">
          <SearchForm className="min-w-0 max-w-[280px] flex-1" />
          <Link
            href={SAVED_NAV_ITEM.href}
            aria-current={pathname === SAVED_NAV_ITEM.href ? 'page' : undefined}
            className={cn(
              'inline-flex min-h-11 min-w-11 shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-pill px-3 text-sm font-semibold text-foreground hover:bg-muted',
              pathname === SAVED_NAV_ITEM.href && 'bg-accent text-accent-ink'
            )}
          >
            <Heart className="h-4 w-4 shrink-0" aria-hidden="true" />
            {/* Icon only at 1024px, where the row is full; the word shows from 1280px. */}
            <span className="sr-only xl:not-sr-only">{SAVED_NAV_ITEM.label}</span>
          </Link>
          <Link
            href={account.href}
            // The dashboard layout verifies the session and loads the profile: never prefetch it from public pages.
            prefetch={account.prefetch}
            className="inline-flex min-h-11 min-w-[6.5rem] shrink-0 items-center justify-center whitespace-nowrap rounded-pill px-3 text-sm font-semibold text-foreground hover:bg-muted"
          >
            {account.label}
          </Link>
          <Link href="/alerts" className={PILL_CTA_CLASS}>
            Get free alerts
          </Link>
        </div>

        <div className="ml-auto flex items-center gap-2 lg:hidden">
          <button
            type="button"
            className={ICON_BUTTON}
            aria-expanded={searchOpen}
            aria-controls="mobile-search"
            aria-label={searchOpen ? 'Close search' : 'Search freebies'}
            onClick={() => setSearchOpen((open) => !open)}
          >
            {searchOpen ? (
              <X className="h-5 w-5" aria-hidden="true" />
            ) : (
              <Search className="h-5 w-5" aria-hidden="true" />
            )}
          </button>

          <DialogPrimitive.Root open={menuOpen} onOpenChange={setMenuOpen}>
            <DialogPrimitive.Trigger className={ICON_BUTTON} aria-label="Open menu">
              <Menu className="h-5 w-5" aria-hidden="true" />
            </DialogPrimitive.Trigger>
            <DialogPrimitive.Portal>
              <DialogOverlay className="bg-foreground/40" />
              <DialogPrimitive.Content
                aria-describedby={undefined}
                className="fixed inset-y-0 right-0 z-50 flex w-full max-w-sm flex-col gap-6 bg-background p-6 shadow-lg data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:slide-out-to-right data-[state=open]:slide-in-from-right"
              >
                <div className="flex items-center justify-between">
                  <DialogPrimitive.Title className="font-display text-xl font-extrabold">
                    Menu
                  </DialogPrimitive.Title>
                  <DialogPrimitive.Close aria-label="Close menu" className={ICON_BUTTON}>
                    <X className="h-5 w-5" aria-hidden="true" />
                  </DialogPrimitive.Close>
                </div>
                <nav aria-label="Main">
                  <ul className="flex flex-col gap-1">
                    {SITE_NAV_ITEMS.map(({ href, label }) => (
                      <li key={href}>
                        <Link
                          href={href}
                          onClick={() => setMenuOpen(false)}
                          className="flex min-h-12 items-center rounded-input px-3 text-base font-semibold hover:bg-muted"
                        >
                          {label}
                        </Link>
                      </li>
                    ))}
                  </ul>
                </nav>
                <div className="mt-auto flex flex-col gap-3">
                  <Link
                    href="/alerts"
                    onClick={() => setMenuOpen(false)}
                    className={PILL_CTA_CLASS}
                  >
                    Get free alerts
                  </Link>
                  <Link
                    href={account.href}
                    prefetch={account.prefetch}
                    onClick={() => setMenuOpen(false)}
                    className="inline-flex min-h-11 items-center justify-center rounded-pill border border-border px-5 text-sm font-semibold hover:bg-muted"
                  >
                    {account.label}
                  </Link>
                </div>
              </DialogPrimitive.Content>
            </DialogPrimitive.Portal>
          </DialogPrimitive.Root>
        </div>
      </div>

      {searchOpen && (
        <div id="mobile-search" className="border-t border-border px-4 py-3 lg:hidden">
          <SearchForm id="mobile-site-search" autoFocus className="w-full" />
        </div>
      )}
    </header>
  );
}
