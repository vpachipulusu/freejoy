'use client';

import { useId } from 'react';
import Link from 'next/link';
import { ArrowRight } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import type { OfferType } from '@/lib/catalogue/types';
import { CTA_LABELS, REQUEST_KIND } from './offer-format';
import { AffiliateCta } from './outbound-link';

interface OfferCtaProps {
  offerType: OfferType;
  /** True when the listing is sponsored: the real-offer CTA says so in a sentence of its own. */
  sponsored?: boolean;
  /** True for fixture offers (design preview); false for real catalogue offers. */
  demo: boolean;
  /** Set for a real offer: the CTA links to `/go/<offerId>`. Null or absent keeps the demo dialog. */
  offerId?: string | null;
  /** The generated postage or card line (`ctaConditions`), shown right after the button. */
  conditions?: string | null;
}

/**
 * Primary call to action. A real offer (`offerId` set) links to the first-party `/go/<offerId>` redirect
 * with the "Ad" label and commission sentence. A demo offer has no destination, so its button opens a
 * "Demo offer" dialog instead: nothing here renders an external href. The ended state is rendered by the
 * (server) hero instead of this client component.
 */
export function OfferCta({
  offerType,
  demo,
  sponsored = false,
  offerId = null,
  conditions = null,
}: OfferCtaProps) {
  const conditionsId = useId();
  if (offerId && !demo) {
    return (
      <AffiliateCta
        offerId={offerId}
        offerType={offerType}
        sponsored={sponsored}
        conditions={conditions}
      />
    );
  }

  return (
    <div>
      <Dialog>
        <DialogTrigger asChild>
          <Button
            type="button"
            variant="accent"
            aria-describedby={conditions ? conditionsId : undefined}
            className="h-14 w-full justify-between rounded-pill px-7 text-base font-bold shadow-sm"
          >
            {CTA_LABELS[offerType]}
            <ArrowRight aria-hidden="true" />
          </Button>
        </DialogTrigger>
        <DialogContent className="rounded-card">
          <DialogTitle>Demo offer</DialogTitle>
          <DialogDescription>
            This is a design preview with sample data, so there is nothing to claim yet.
          </DialogDescription>
          <ul className="list-disc space-y-2 ps-5 text-sm text-foreground">
            <li>
              Real offers will link to the provider through a Freejoy redirect, with an affiliate
              disclosure next to the button.
            </li>
            <li>Nothing was clicked through and no other website was opened.</li>
          </ul>
          <DialogClose asChild>
            <Button type="button" variant="outline" className="rounded-pill">
              Got it
            </Button>
          </DialogClose>
        </DialogContent>
      </Dialog>
      {conditions && (
        <p id={conditionsId} className="mt-3 px-3 text-xs font-semibold text-foreground">
          {conditions}
        </p>
      )}
      <p className="mt-3 px-3 text-xs text-muted-foreground">
        Demo offer: this button stays on Freejoy. A real offer will take you to the brand&apos;s
        website to request your {REQUEST_KIND[offerType]}.
      </p>
      <p className="mt-1 px-3 text-xs text-muted-foreground">
        <Link href="/affiliate-disclosure" className="underline underline-offset-2">
          Affiliate disclosure
        </Link>
        : real offers are marked &quot;Ad&quot; and say how Freejoy is paid.
      </p>
    </div>
  );
}
