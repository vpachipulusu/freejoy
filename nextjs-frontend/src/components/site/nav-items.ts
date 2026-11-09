import { Bell, Heart, Home, Search, type LucideIcon } from 'lucide-react';

export interface SiteNavItem {
  href: string;
  label: string;
  /** Left out of the header row below 1200px, where the row is full (the menu sheet still lists it). */
  wideOnly?: boolean;
}

export interface SiteTabItem extends SiteNavItem {
  icon: LucideIcon;
}

/** Header navigation (design screens 01-04). Only routes that exist have an entry. */
export const SITE_NAV_ITEMS: ReadonlyArray<SiteNavItem> = [
  { href: '/freebies', label: 'All freebies' },
  { href: '/freebies#categories', label: 'Categories' },
  { href: '/freebies?sort=newest', label: 'Just added' },
  { href: '/about#how-it-works', label: 'How it works', wideOnly: true },
];

/** The Saved list (FREEJ-28): the header's desktop entry point and the third phone tab. */
export const SAVED_NAV_ITEM: SiteNavItem = { href: '/saved', label: 'Saved' };

/**
 * Phone tab bar (design screens 05 to 07): Home, Browse, Saved, Alerts. Categories left the bar when
 * Saved took the design's third slot; they stay in the menu sheet, the home page grid and /freebies.
 */
export const SITE_TAB_ITEMS: ReadonlyArray<SiteTabItem> = [
  { href: '/', label: 'Home', icon: Home },
  { href: '/freebies', label: 'Browse', icon: Search },
  { ...SAVED_NAV_ITEM, icon: Heart },
  { href: '/alerts', label: 'Alerts', icon: Bell },
];

export const FOOTER_LINKS: ReadonlyArray<SiteNavItem> = [
  { href: '/about', label: 'About' },
  { href: '/contact', label: 'Contact' },
  { href: '/privacy', label: 'Privacy' },
  { href: '/cookies', label: 'Cookies' },
  { href: '/terms', label: 'Terms' },
  { href: '/affiliate-disclosure', label: 'Affiliate disclosure' },
];

/** The tab whose path equals the current path or is a parent segment of it ("/" matches only itself). */
export function activeTabHref(pathname: string | null): string | null {
  if (!pathname) return null;
  let best: string | null = null;
  for (const { href } of SITE_TAB_ITEMS) {
    const matches = pathname === href || (href !== '/' && pathname.startsWith(`${href}/`));
    if (matches && (best === null || href.length > best.length)) best = href;
  }
  return best;
}
