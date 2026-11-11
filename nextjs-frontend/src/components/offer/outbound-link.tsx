import { useId } from 'react';
import Link from 'next/link';
import { ArrowRight } from 'lucide-react';
import { buttonVariants } from '@/components/ui/button';
import type { OfferType } from '@/lib/catalogue/types';
import { cn } from '@/lib/utils';
import { CTA_LABELS, REQUEST_KIND } from './offer-format';

/** Every anchor to `/go/<id>` carries these: paid links are `sponsored`, the owner also wants `nofollow`. */
export const OUTBOUND_LINK_REL = 'sponsored nofollow noopener';

interface OutboundLinkProps {
  offerId: string;
  /**
   * False when the page rendering this link shows no commission disclosure. The link then carries `?nd=1`
   * ("not disclosed"), and `/go/` turns the visitor back to the offer page if the offer does earn commission
   * after all. True gives the plain link.
   */
  commissionDisclosed: boolean;
  className?: string;
  /** The id of text that describes the link (for example the postage line). Forwarded as-is. */
  'aria-describedby'?: string;
  children: React.ReactNode;
}

/**
 * The one place that renders a link to the first-party redirect. It is a plain `<a>`, not `next/link`:
 * `Link` prefetches when it scrolls into view, which would load `/go/<id>` and could count a click the
 * visitor never made.
 */
export function OutboundLink({
  offerId,
  commissionDisclosed,
  className,
  'aria-describedby': describedBy,
  children,
}: OutboundLinkProps) {
  return (
    <a
      href={`/go/${encodeURIComponent(offerId)}${commissionDisclosed ? '' : '?nd=1'}`}
      rel={OUTBOUND_LINK_REL}
      className={className}
      aria-describedby={describedBy}
    >
      {children}
    </a>
  );
}

interface AffiliateCtaProps {
  offerId: string;
  offerType: OfferType;
  /** True for a sponsored listing: adds the sentence that says the brand pays for it. */
  sponsored: boolean;
  /**
   * True when the offer earns Freejoy a commission (or that is unknown). Only then are the "Ad" label and the
   * commission sentence shown. Required: the caller decides how an unknown flag is treated (`OfferHero` passes
   * `earnsCommission !== false`), so the fail-safe lives in one place.
   */
  earnsCommission: boolean;
  /** The generated postage or card line (`ctaConditions`), shown right after the button. */
  conditions?: string | null;
}

/**
 * Call to action for a real offer. When the followed link earns commission the "Ad" label comes first, then the
 * button, then the commission sentence (docs/compliance/uk-affiliate-checklist.md A1, A2). When it does not (a
 * direct link to the brand), none of that appears: there is nothing to disclose. A sponsored listing keeps its
 * own sentence either way and adds "may also earn a commission" only when the link earns. Wording is for the
 * legal reviewer to confirm.
 */
export function AffiliateCta({
  offerId,
  offerType,
  sponsored,
  earnsCommission,
  conditions = null,
}: AffiliateCtaProps) {
  const conditionsId = useId();
  const sponsoredId = useId();
  const commissionId = useId();
  const commissionSentence = !sponsored && earnsCommission;
  const describedBy = [
    conditions ? conditionsId : null,
    sponsored ? sponsoredId : null,
    commissionSentence ? commissionId : null,
  ]
    .filter(Boolean)
    .join(' ');
  const followsMutedSentence = sponsored || earnsCommission;
  return (
    <div>
      {earnsCommission && (
        <p className="mb-2 px-3 text-xs font-bold uppercase tracking-wider text-foreground">Ad</p>
      )}
      <OutboundLink
        offerId={offerId}
        commissionDisclosed={earnsCommission}
        aria-describedby={describedBy || undefined}
        className={cn(
          buttonVariants({ variant: 'accent' }),
          'h-14 w-full justify-between rounded-pill px-7 text-base font-bold shadow-sm'
        )}
      >
        {CTA_LABELS[offerType]}
        <ArrowRight aria-hidden="true" />
      </OutboundLink>
      {conditions && (
        <p id={conditionsId} className="mt-3 px-3 text-xs font-semibold text-foreground">
          {conditions}
        </p>
      )}
      {sponsored && (
        <p id={sponsoredId} className="mt-3 px-3 text-xs text-muted-foreground">
          This is a sponsored listing: the brand pays Freejoy, or gives it a benefit, for it to
          appear here.
          {earnsCommission && ' Freejoy may also earn a commission if you use this link.'}
        </p>
      )}
      {commissionSentence && (
        <p id={commissionId} className="mt-3 px-3 text-xs text-muted-foreground">
          Freejoy is paid a commission by the brand if you take up this offer through this link.
        </p>
      )}
      <p
        className={cn('px-3 text-xs text-muted-foreground', followsMutedSentence ? 'mt-1' : 'mt-3')}
      >
        This takes you to the brand&apos;s website to request your {REQUEST_KIND[offerType]}.{' '}
        <Link href="/affiliate-disclosure" className="underline underline-offset-2">
          Affiliate disclosure
        </Link>
      </p>
    </div>
  );
}
