import type { Metadata } from 'next';
import Link from 'next/link';
import { LegalPage } from '@/components/legal/legal-page';
import { isAlertsFormLive } from '@/lib/alerts/live';
import { getCatalogueSource } from '@/lib/catalogue';

export const metadata: Metadata = {
  title: 'About Freejoy',
  description: 'What Freejoy is, how we look at offers and how to get the most from the site.',
  robots: { index: false },
};

/**
 * Rendered per request: two server-only switches change what is true here (CATALOGUE_SOURCE decides whether the
 * listings are samples, ALERTS_FORM_LIVE whether the alerts form works), and both are read when the server
 * runs, not when it is built.
 */
export const dynamic = 'force-dynamic';

const STEPS = [
  {
    title: 'Find something you like',
    detail: 'Browse by category or search for a brand. Each listing says what the offer is.',
  },
  {
    title: 'Read the terms first',
    detail:
      'Postage, eligibility, card requirements and end dates are set out on each offer page before you click through.',
  },
  {
    title: 'Claim it on the brand’s site',
    detail:
      'You finish the claim with the brand itself. Freejoy does not collect your claim details.',
  },
];

export default async function AboutPage() {
  const sampleListings = (await getCatalogueSource()) === 'fixture';
  const alertsLive = isAlertsFormLive();

  return (
    <LegalPage
      title="About Freejoy"
      summary="Freejoy is a small UK guide to freebies, samples, trials and offers worth a look."
      lastReviewed="Not yet reviewed"
    >
      <h2>What Freejoy is</h2>
      <p>
        Freejoy is planned to list free samples, free trials, cashback, vouchers and competitions
        from brands people in the UK might enjoy, with the terms in plain words next to each one. We
        do not run the offers; the brand does.
      </p>
      <h2>Not everything is free</h2>
      <p>
        Each listing says which kind of offer it is. A free sample may have postage to pay. A free
        trial may ask for a card and start charging when it ends. Cashback and vouchers are not
        free: you spend money first or get a discount. A competition is an entry, not a prize.
      </p>
      <h2>How we intend to check offers</h2>
      <p>
        The plan: an editor reads the brand&apos;s own page and notes the offer type, any postage,
        whether a card is needed and who can claim.{' '}
        {sampleListings &&
          'This release is a design preview with sample listings, so no offer shown here has been through that process yet. '}
        Offers change without notice, so always read the brand&apos;s terms before you claim.
      </p>
      <h2 id="how-it-works">How it works</h2>
      <ol>
        {STEPS.map((step) => (
          <li key={step.title}>
            <strong>{step.title}.</strong> {step.detail}
          </li>
        ))}
      </ol>
      <h2>How Freejoy plans to pay for itself</h2>
      <p>
        Only some links to brands are expected to earn Freejoy a commission once real offers go
        live, and those links are marked &ldquo;Ad&rdquo;. Read the{' '}
        <Link href="/affiliate-disclosure">affiliate disclosure</Link> for how that is planned to
        work.
      </p>
      <h2>Keeping in touch</h2>
      <p>
        {alertsLive ? (
          <>
            You can sign up for email alerts with the <Link href="/alerts">alerts form</Link>. We
            email a confirmation link first, and nothing starts until you open it.
          </>
        ) : (
          <>
            Email alerts are not open yet. The <Link href="/alerts">alerts page</Link> says so, and
            nothing can be entered or saved there until they launch.
          </>
        )}{' '}
        The <Link href="/contact">contact page</Link> will explain how to tell us about a problem
        with a listing once it is live.
      </p>
    </LegalPage>
  );
}
