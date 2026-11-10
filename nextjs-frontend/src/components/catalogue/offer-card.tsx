import { useId } from 'react';
import Link from 'next/link';
import { ArrowRight, Clock } from 'lucide-react';
import { postageText } from '@/components/offer/offer-format';
import { SaveButton } from '@/components/saved/save-button';
import { cn } from '@/lib/utils';
import { displayFreshness } from '@/lib/catalogue/freshness';
import type { CardOffer, Merchant } from '@/lib/catalogue/types';
import { DemoBadge } from './demo-badge';
import { OfferArt } from './offer-art';
import { OfferTypeBadge } from './offer-type-badge';
import { SponsoredBadge } from './sponsored-badge';

interface OfferCardProps {
  /** Only the card fields: the Saved page sends no more than this to the browser. */
  offer: CardOffer;
  merchant: Merchant;
  className?: string;
  /** Pass `false` to stop the title link prefetching its page. Left undefined, Next.js decides, as before. */
  prefetch?: boolean;
}

export function offerHref(offer: Pick<CardOffer, 'slug'>): string {
  return `/freebies/${offer.slug}`;
}

/**
 * Image-led card (design screens 01, 02, 05, 06): type badge over the art, merchant eyebrow,
 * title, a freshness label (see `displayFreshness`) and a "View freebie" link. The whole card is one link
 * target; the demo badge is visible on fixture offers. The heart (`SaveButton`, FREEJ-28) is the card's only
 * other control: a client island beside the link, never inside it. The card itself stays a server component.
 */
export function OfferCard({ offer, merchant, className, prefetch }: OfferCardProps) {
  const href = offerHref(offer);
  const sponsoredId = useId();
  const freshness = displayFreshness(offer);
  return (
    <article
      className={cn(
        'focus-proxy group/card relative flex flex-col rounded-card border border-border bg-card p-4 shadow-sm transition-shadow hover:shadow-md',
        className
      )}
    >
      <div className="relative">
        {/* The art and the demo badge share a box of their own, so the type badge below can leave the art. */}
        <div className="relative">
          <OfferArt image={offer.image} />
          {/* The heart owns the top-right corner (design screens 05, 06); the demo badge takes the bottom-right. */}
          {offer.demo && <DemoBadge className="absolute bottom-3 right-3" />}
        </div>
        {/* Over the art's top-left; under 375px (a card too narrow for badge and heart side by side) it drops
            below the art instead, as a bordered pill. */}
        <OfferTypeBadge
          type={offer.offerType}
          className="absolute left-2 top-2 whitespace-nowrap px-1.5 text-[10px] tracking-normal max-[374px]:static max-[374px]:mt-2 max-[374px]:border max-[374px]:border-border sm:left-3 sm:top-3 sm:px-3 sm:text-[11px] sm:tracking-wide"
        />
        <SaveButton slug={offer.slug} title={offer.title} className="absolute right-0 top-0" />
      </div>
      <p className="mt-4 text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
        {merchant.name}
      </p>
      {/* In the flow, not over the art, so it cannot sit on the type badge or the heart. */}
      {offer.sponsored && <SponsoredBadge id={sponsoredId} className="mt-1.5 self-start" />}
      <h3 className="mt-1.5 font-display text-base font-bold leading-snug text-foreground sm:text-lg">
        {/* Stretched link: its ::after covers the card, so the card draws the one focus outline (the focus-proxy rule in globals.css) and the link draws none. */}
        <Link
          href={href}
          prefetch={prefetch}
          aria-describedby={offer.sponsored ? sponsoredId : undefined}
          className="after:absolute after:inset-0 after:rounded-card focus-visible:outline-none"
        >
          {offer.title}
        </Link>
      </h3>
      {/* Strict CAP 3.23 reading (checklist D-07): a "Free sample" badge never appears without its postage. */}
      {offer.offerType === 'free_sample' && (
        <p className="mt-1 text-xs text-muted-foreground">Postage: {postageText(offer.postage)}</p>
      )}
      <div className="mt-auto flex items-center justify-between gap-2 border-t border-border pt-3 text-xs">
        {/* The mobile design (screens 05/06) keeps only the link on narrow cards. */}
        {freshness ? (
          <span className="hidden items-center gap-1 whitespace-nowrap text-muted-foreground sm:inline-flex">
            <Clock className="h-3.5 w-3.5" aria-hidden="true" />
            {freshness}
          </span>
        ) : (
          <span />
        )}
        <span className="inline-flex items-center gap-1 font-bold text-accent-ink group-hover/card:underline group-has-[button:hover]/card:no-underline">
          View freebie
          <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
        </span>
      </div>
    </article>
  );
}

interface OfferGridProps {
  offers: CardOffer[];
  merchants: Merchant[];
  className?: string;
  /** Passed to every card; the Saved page turns it off so it does not prefetch the whole list. */
  prefetch?: boolean;
}

/** Two columns on phones (10px gap in the design), three at tablet, four on desktop. */
export function OfferGrid({ offers, merchants, className, prefetch }: OfferGridProps) {
  const bySlug = new Map(merchants.map((merchant) => [merchant.slug, merchant]));
  return (
    <ul
      className={cn('grid grid-cols-2 gap-2.5 sm:gap-5 md:grid-cols-3 xl:grid-cols-4', className)}
    >
      {offers.map((offer) => {
        // API offers carry their merchant; fixture offers are looked up by slug.
        const merchant = offer.merchant ??
          bySlug.get(offer.merchantSlug) ?? {
            slug: offer.merchantSlug,
            name: offer.merchantSlug,
            logoUrl: null,
          };
        return (
          <li key={offer.id} className="flex">
            <OfferCard offer={offer} merchant={merchant} className="w-full" prefetch={prefetch} />
          </li>
        );
      })}
    </ul>
  );
}
