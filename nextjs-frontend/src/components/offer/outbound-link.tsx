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
  className,
  'aria-describedby': describedBy,
  children,
}: OutboundLinkProps) {
  return (
    <a
      href={`/go/${encodeURIComponent(offerId)}`}
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
  sponsored?: boolean;
  /** The generated postage or card line (`ctaConditions`), shown right after the button. */
  conditions?: string | null;
}

/**
 * Call to action for a real offer: the "Ad" label comes first, then the button, then the commission
 * sentence (docs/compliance/uk-affiliate-checklist.md A1, A2). Wording is for the legal reviewer to confirm.
 */
export function AffiliateCta({
  offerId,
  offerType,
  sponsored = false,
  conditions = null,
}: AffiliateCtaProps) {
  const conditionsId = useId();
  const sponsoredId = useId();
  const describedBy = [conditions ? conditionsId : null, sponsored ? sponsoredId : null]
    .filter(Boolean)
    .join(' ');
  return (
    <div>
      <p className="mb-2 px-3 text-xs font-bold uppercase tracking-wider text-foreground">Ad</p>
      <OutboundLink
        offerId={offerId}
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
      {sponsored ? (
        <p id={sponsoredId} className="mt-3 px-3 text-xs text-muted-foreground">
          This is a sponsored listing: the brand pays Freejoy, or gives it a benefit, for it to
          appear here. Freejoy may also earn a commission if you use this link.
        </p>
      ) : (
        <p className="mt-3 px-3 text-xs text-muted-foreground">
          Freejoy is paid a commission by the brand if you take up this offer through this link.
        </p>
      )}
      <p className="mt-1 px-3 text-xs text-muted-foreground">
        This takes you to the brand&apos;s website to request your {REQUEST_KIND[offerType]}.{' '}
        <Link href="/affiliate-disclosure" className="underline underline-offset-2">
          Affiliate disclosure
        </Link>
      </p>
    </div>
  );
}
