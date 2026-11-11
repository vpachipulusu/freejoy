import type { Metadata } from 'next';
import Link from 'next/link';
import { LegalPage } from '@/components/legal/legal-page';
import { NOINDEX_ROBOTS, openGraphFor } from '@/lib/seo/metadata';

const TITLE = 'List your free sample';
const DESCRIPTION =
  'How retailers and brands can ask Freejoy to list a free sample, trial or similar offer for people in the UK, and what every listing must state.';

/**
 * Static page, no form and no data collected: a submission form with an admin queue is a later story.
 * Like the other editorial pages it stays out of search engines while it carries the draft banner.
 */
export const metadata: Metadata = {
  title: TITLE,
  description: DESCRIPTION,
  alternates: { canonical: '/partners' },
  openGraph: openGraphFor({ title: TITLE, description: DESCRIPTION, path: '/partners' }),
  robots: NOINDEX_ROBOTS,
};

export default function PartnersPage() {
  return (
    <LegalPage
      title={TITLE}
      summary="How to ask Freejoy to list a free sample, a trial or a similar offer for people in the UK."
      lastReviewed="Not yet reviewed"
    >
      <h2>What Freejoy lists</h2>
      <p>
        Freejoy lists free samples, free trials and similar offers for people who live in the UK. If
        it costs anything, such as postage, the listing says so.
      </p>
      <h2>What a listing must state</h2>
      <p>Every listing sets these out before anyone clicks through to your site.</p>
      <ul>
        <li>The postage cost, if there is any.</li>
        <li>Whether a payment card is needed.</li>
        <li>Any subscription or charge that follows the sample or trial.</li>
        <li>Who is eligible.</li>
        <li>How long the offer runs, or how many are available.</li>
      </ul>
      <h2>How listings are checked and published</h2>
      <p>
        Freejoy&apos;s editors check and write every listing before it is published. They check it
        again after it goes live. A retailer cannot publish a listing directly.
      </p>
      <p>A listing ends when the sample runs out or the offer closes.</p>
      <h2>Paid and sponsored listings</h2>
      <p>
        A listing that a retailer pays for, or gives Freejoy any other benefit for, is labelled
        &ldquo;Sponsored&rdquo;. Links that earn Freejoy a commission through an affiliate network
        carry an &ldquo;Ad&rdquo; label, and a sponsored listing with such a link shows both.
        Payment does not change the checks. Every listing gets the same checks.
      </p>
      <p>
        The <Link href="/affiliate-disclosure">affiliate disclosure</Link> explains how Freejoy
        earns money from links.
      </p>
      <h2>What we do not do</h2>
      <p>
        Freejoy does not collect visitors&apos; names or addresses on a retailer&apos;s behalf.
        People claim the offer on your own site, and you look after their details there.
      </p>
      <h2>Get in touch</h2>
      <p>
        The <Link href="/contact">contact page</Link> will show how to reach Freejoy once a contact
        route is published. When it does, use it to ask for a listing or to tell us an offer has
        ended. Say what the offer is and where its terms are on your own site.
      </p>
      <p>There is no listing form on this page, and it does not ask you for any details.</p>
      <p>
        If your technical team wants to read your click counts, the{' '}
        <Link href="/developers/partner-api">partner API documentation</Link> explains how. The
        partner API is not open to every retailer yet.
      </p>
    </LegalPage>
  );
}
