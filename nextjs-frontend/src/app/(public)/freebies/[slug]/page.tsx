import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import { CatalogueUnavailablePage } from '@/components/catalogue/inline-error';
import { Breadcrumbs } from '@/components/offer/breadcrumbs';
import { ClaimSteps } from '@/components/offer/claim-steps';
import { EndedOfferView } from '@/components/offer/ended-offer';
import { ImportantBits } from '@/components/offer/important-bits';
import { OfferHero } from '@/components/offer/offer-hero';
import { RelatedOffers } from '@/components/offer/related-offers';
import { JsonLd } from '@/components/seo/json-ld';
import {
  getCategoryBySlug,
  getMerchantBySlug,
  getMerchants,
  getRelatedOffers,
  getToday,
  isLive,
  lookupOffer,
  settle,
} from '@/lib/catalogue';
import { forcesDisclosure } from '@/lib/outbound/disclosure';
import { endedOfferState } from '@/lib/seo/indexing';
import { buildBreadcrumbList } from '@/lib/seo/json-ld';
import { NOINDEX_FOLLOW_ROBOTS, offerRobots, openGraphFor } from '@/lib/seo/metadata';

/**
 * Rendered per request. The offer read behind it is cached for a minute, so an offer that has just
 * ended shows its ended state and `noindex` within that time, and an outage is never baked into a
 * prerendered page.
 */
export const dynamic = 'force-dynamic';

interface OfferPageProps {
  params: Promise<{ slug: string }>;
  /** Only `ad` is read, and only to ADD the commission disclosure (see `@/lib/outbound/disclosure`). */
  searchParams?: Promise<{ ad?: string | string[] }>;
}

const NOT_FOUND_METADATA: Metadata = { title: 'Offer not found', robots: { index: false } };
// Outage: the page is not known, so it asks not to be indexed but lets its links be followed. (The listing
// pages keep their normal robots through an outage; see docs/developer/freejoy-seo.md.)
const UNAVAILABLE_METADATA: Metadata = {
  title: 'Freebie unavailable',
  robots: NOINDEX_FOLLOW_ROBOTS,
};

export async function generateMetadata({
  params,
}: Pick<OfferPageProps, 'params'>): Promise<Metadata> {
  const { slug } = await params;
  const [lookup, today] = await Promise.all([settle(lookupOffer(slug)), getToday()]);
  if (!lookup.ok) return UNAVAILABLE_METADATA;

  const result = lookup.data;
  if (result.kind === 'unreadable') return UNAVAILABLE_METADATA;
  if (result.kind === 'not_found') return NOT_FOUND_METADATA;
  if (result.kind === 'ended') {
    // More than 30 days after it ended the page is gone: a 404 for now (no real 410, see the SEO doc).
    if (endedOfferState(result.ended, today) === 'gone') return NOT_FOUND_METADATA;
    const path = `/freebies/${result.ended.slug}`;
    const description = `This offer has ended. See more ${result.ended.categoryName} freebies on Freejoy.`;
    return {
      title: result.ended.title,
      description,
      alternates: { canonical: path },
      openGraph: openGraphFor({ title: result.ended.title, description, path, type: 'article' }),
      robots: NOINDEX_FOLLOW_ROBOTS,
    };
  }

  const { offer } = result;
  const path = `/freebies/${offer.slug}`;
  // A paid listing says so in the text a search result or a shared link shows.
  const description = offer.sponsored ? `Sponsored listing. ${offer.summary}` : offer.summary;
  return {
    title: offer.title,
    description,
    alternates: { canonical: path },
    openGraph: openGraphFor({
      title: offer.title,
      description,
      path,
      type: 'article',
    }),
    // Indexable only for a live API offer; ended offers are noindex, follow (see lib/seo).
    robots: offerRobots(offer, today),
  };
}

export default async function OfferPage({ params, searchParams }: OfferPageProps) {
  const { slug } = await params;
  const disclosureForced = forcesDisclosure((await searchParams)?.ad);
  const lookup = await settle(lookupOffer(slug));
  if (!lookup.ok) return <CatalogueUnavailablePage heading="This freebie" />;

  const result = lookup.data;
  // The API has this offer but this site cannot read it: an outage, not a missing page.
  if (result.kind === 'unreadable') return <CatalogueUnavailablePage heading="This freebie" />;
  if (result.kind === 'not_found') notFound();
  if (result.kind === 'ended') {
    if (endedOfferState(result.ended, await getToday()) === 'gone') notFound();
    return <EndedOfferView ended={result.ended} />;
  }

  const { offer } = result;
  // `?ad=1` (the bounce from a stale link) can only add the disclosure: the hero renders as if the offer earns.
  const heroOffer = disclosureForced ? { ...offer, earnsCommission: true } : offer;
  const [categoryRead, relatedRead, merchantsRead, today] = await Promise.all([
    settle(getCategoryBySlug(offer.categorySlug)),
    settle(getRelatedOffers(offer, 4)),
    settle(getMerchants()),
    getToday(),
  ]);
  if (!categoryRead.ok) return <CatalogueUnavailablePage heading={offer.title} />;
  const category = categoryRead.data;
  const merchant = offer.merchant ?? (await getMerchantBySlug(offer.merchantSlug));
  if (!merchant || !category) notFound();

  // Related offers are a nicety: if they cannot be read the offer itself still renders.
  const related = relatedRead.ok ? relatedRead.data : [];
  const merchants = merchantsRead.ok ? merchantsRead.data : [];
  const ended = !isLive(offer, today);

  return (
    <div className="mx-auto max-w-content px-4 py-6 sm:px-6 sm:py-10">
      <JsonLd
        data={buildBreadcrumbList([
          { name: 'Home', path: '/' },
          { name: category.name, path: `/category/${category.slug}` },
          { name: offer.title, path: `/freebies/${offer.slug}` },
        ])}
      />
      <Breadcrumbs category={category} title={offer.title} />
      <div className="mt-6 space-y-12 sm:mt-8 sm:space-y-16">
        <OfferHero offer={heroOffer} merchant={merchant} category={category} ended={ended} />
        <ClaimSteps steps={offer.steps} />
        <ImportantBits offer={offer} />
        <RelatedOffers offers={related} merchants={merchants} category={category} />
      </div>
    </div>
  );
}
