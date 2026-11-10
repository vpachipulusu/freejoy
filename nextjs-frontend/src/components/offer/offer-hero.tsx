import { DemoBadge } from '@/components/catalogue/demo-badge';
import { OfferArt } from '@/components/catalogue/offer-art';
import { OfferTypeBadge } from '@/components/catalogue/offer-type-badge';
import { SaveButton } from '@/components/saved/save-button';
import type { Category, Merchant, Offer } from '@/lib/catalogue/types';
import { OfferCta } from './offer-cta';
import { ctaConditions } from './offer-format';
import { OfferEndedPanel } from './offer-ended-panel';
import { OfferTrustPanel } from './offer-trust-panel';

interface OfferHeroProps {
  offer: Offer;
  merchant: Merchant;
  category: Category;
  ended: boolean;
}

/** Art on the left, copy on the right from 1024px; stacked on phones (design screens 03, 07). */
export function OfferHero({ offer, merchant, category, ended }: OfferHeroProps) {
  return (
    <section className="grid gap-8 lg:grid-cols-2 lg:items-start lg:gap-14">
      <div className="relative">
        <OfferArt image={offer.image} size="hero" />
        <OfferTypeBadge
          type={offer.offerType}
          className="absolute left-4 top-4 sm:left-6 sm:top-6"
        />
        {offer.demo && <DemoBadge className="absolute bottom-4 right-4 sm:bottom-6 sm:right-6" />}
        <SaveButton
          slug={offer.slug}
          title={offer.title}
          className="absolute right-1 top-1 sm:right-3 sm:top-3"
        />
      </div>
      <div className="flex flex-col gap-5 lg:pt-4">
        <p className="text-[11px] font-bold uppercase tracking-wider text-accent-ink">
          {merchant.name} / {category.name}
        </p>
        <h1 className="font-display text-3xl font-extrabold leading-tight tracking-tight sm:text-5xl sm:leading-[1.1]">
          {offer.title}
        </h1>
        <p className="max-w-xl text-base text-muted-foreground sm:text-lg">{offer.summary}</p>
        <OfferTrustPanel offer={offer} />
        {ended ? (
          <OfferEndedPanel categorySlug={category.slug} />
        ) : (
          <OfferCta
            offerType={offer.offerType}
            demo={offer.demo}
            offerId={offer.demo ? null : offer.id}
            conditions={ctaConditions(offer)}
          />
        )}
      </div>
    </section>
  );
}
