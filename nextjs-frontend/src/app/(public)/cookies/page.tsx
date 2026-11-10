import type { Metadata } from 'next';
import { LegalPage, ReviewerChecklist } from '@/components/legal/legal-page';

export const metadata: Metadata = {
  title: 'Cookie notice',
  description: 'Draft cookie notice for Freejoy, pending legal review.',
  robots: { index: false },
};

export default function CookiesPage() {
  return (
    <LegalPage
      title="Cookie notice"
      summary="What Freejoy stores in your browser. This is a draft."
      lastReviewed="Not yet reviewed"
    >
      <h2>Cookies in this release</h2>
      <p>
        This release is intended to set no non-essential cookies and run no analytics or advertising
        scripts. If error monitoring is switched on, the privacy notice describes it. Browsing the
        public pages is not meant to store anything on your device, apart from the saved list
        described below, which exists only if you use the heart button, so no cookie banner is shown
        at present. That is our current reading and it is still to be confirmed in review.
      </p>
      <h2>If you save freebies</h2>
      <p>
        The heart on a freebie saves it to a list on this device. The list is kept in your
        browser&apos;s local storage under the name <code>freejoy.saved.v1</code>. It is not a
        cookie. It holds only the page names (slugs) of the freebies you saved, up to 200, and
        nothing about you. It stays until you remove the freebies or clear your browser data, and it
        is not copied to your other devices. It is not stored in a database or against an account.
        The Saved page sends those names to our server only to look the freebies up; we do not keep
        them, but looking them up produces ordinary server log entries, as any page request does.
        Our application keeps its log for 14 days and its error log for 30 days; how long our
        hosting service and our backend keep theirs will be added here. If error reporting is
        switched on, page addresses of freebies you open can appear in error and timing reports. We
        treat this storage as something you asked for, which is our current reading and is still to
        be confirmed in review.
      </p>
      <h2>If you sign in</h2>
      <p>
        If you sign in to an account, a sign-in cookie called `session` keeps you signed in for up
        to five days. It is needed for the sign-in you asked for and is not used for anything else.
        Opening the sign-in or sign-up page prepares sign-in storage in your browser even if you do
        not sign in; it is used only for signing in. The final list will name each item.
      </p>
      <h2>If that changes</h2>
      <p>
        If analytics or other optional cookies are added later, this page will list each one, what
        it is for and how long it lasts, and we will ask for your consent before setting it unless a
        legal exception clearly applies and we say so.
      </p>
      <ReviewerChecklist
        items={[
          'Final cookie and local-storage list, including strictly necessary items from the framework and sign-in',
          'Whether the conclusion that no banner is needed on the public pages holds',
          'Whether the saved-freebies list in local storage (freejoy.saved.v1) is storage the visitor asked for, so that no consent is needed',
          'Retention of the server logs of the backend and the hosting service for Saved page lookups (the periods for the application itself are stated above), and whether page addresses of freebies a visitor opens in error and timing reports need wording',
          'Whether error monitoring needs any wording here or only in the privacy notice',
          'Consent mechanism and wording before any optional cookie, analytics or affiliate pixel is added',
          'Expiry period stated for each item',
        ]}
      />
    </LegalPage>
  );
}
