import type { Metadata } from 'next';
import { LegalPage, ReviewerChecklist } from '@/components/legal/legal-page';
import { CONFIRM_WINDOW_TEXT } from '@/lib/alerts/consent';

export const metadata: Metadata = {
  title: 'Privacy notice',
  description: 'Draft privacy notice for Freejoy, pending legal review.',
  robots: { index: false },
};

export default function PrivacyPage() {
  return (
    <LegalPage
      title="Privacy notice"
      summary="How Freejoy intends to handle personal information. This is a draft."
      lastReviewed="Not yet reviewed"
    >
      <h2>What this release collects</h2>
      <p>
        If you sign up for alerts we collect the email address you enter, the categories and how
        often you want to hear from us, and a record of your agreement: the exact wording you saw,
        when you agreed and which form you used. We do not store your IP address in that record. The
        contact form is switched off. Accounts are different: see the next section.
      </p>
      <p>
        Like any website, the service that hosts Freejoy handles technical details of each request,
        such as your IP address and browser type, to deliver pages and keep the site secure. If
        error reporting is switched on, details of a fault, and a small sample of page-load timing
        reports, which include the page address and browser details, may be sent to our
        error-monitoring provider. Who these providers are and how long they keep the details will
        be added here.
      </p>
      <p>
        The email address you give for alerts is sent to us in the body of a request, never in the
        page address. The links in our emails do carry a single-use code in the page address, so
        that code can appear in request logs of our host; it stops working once it has been used.
        The search box is different: your search words travel in the page address, so an email
        address typed there can appear in request logs of our host and in your browser history. Any
        email address found in a page address, and that single-use code, is removed before an error
        or timing report is sent to our error-monitoring provider.
      </p>
      <p>
        If you use the heart on a freebie, the list of freebies you saved stays in your browser, on
        your device. The Saved page sends their page names to our server only to look the freebies
        up; we do not keep that list or link it to you, and we record no analytics event when you
        save or remove one. See the cookie notice for the storage name.
      </p>
      <h2>If you create an account</h2>
      <p>
        If you create an account, we hold your email address, your name if you give one, the times
        you sign in, and the IP address and browser of each sign-in. If you switch on two-factor
        sign-in, we also hold those settings. How long each of these is kept will be added here.
      </p>
      <h2>Email alerts</h2>
      <p>
        We use a double opt-in. After you submit the alerts form we email you a confirmation link,
        and you are added to the alert list only when you open it and press the button. The link
        works for {CONFIRM_WINDOW_TEXT} and once. Until then we send nothing else. We keep the
        details above so that we can send the alerts you asked for and show that you asked.
      </p>
      <p>
        Every alert email carries an unsubscribe link, and mail apps that offer a one-click
        unsubscribe button can use it. Proposed retention, for legal review: a sign-up that is never
        confirmed is deleted after 30 days. If you unsubscribe, we keep your address on a
        do-not-email list for 12 months, only so that it is not emailed again, and then delete it.
        Subscribers who stay subscribed are kept until they unsubscribe.
      </p>
      <p>
        We plan to use an email provider to send these emails and hold the alert list. The provider
        currently proposed is Brevo, which hosts its data in the EU. This is not yet confirmed.
      </p>
      <p>Alerts are a plan, not something running today.</p>
      <h2>If you use an offer button</h2>
      <p>
        When you use an offer&apos;s button to go to a brand, Freejoy counts the click. The record
        holds which offer, the time to the nearest minute, the name of the website you came from
        (not the page) and a rough device type. It holds no IP address, no cookie identifier and no
        account identifier, and the redirect sets no cookie. Click records are deleted after 13
        months (395 days). The brand&apos;s website and the affiliate network that tracks the click
        are separate and may use their own cookies.
      </p>
      <p>
        Details of who processes this data, how long it is kept and how to ask for it to be removed
        will be added here.
      </p>
      <h2>Your rights</h2>
      <p>
        Under UK data protection law you have rights to access, correct and delete your personal
        information, to object to direct marketing and, where we rely on your consent, to withdraw
        it. You can also complain to the Information Commissioner&apos;s Office. The final wording
        and a contact route will be added after review.
      </p>
      <ReviewerChecklist
        items={[
          'Controller identity (legal name and address) and the data protection contact route',
          'Lawful basis for each purpose (consent for alerts, legitimate interests for site security)',
          'Whether the outbound-click counts are personal data, and the 13-month (395 day) retention period for click records',
          'The alerts consent wording (stored as proof of consent with each sign-up) and the 30-day and 12-month retention periods for alert subscribers',
          'Retention periods for confirmation-token, contact and sign-in history data',
          'Named processors (hosting, error monitoring, email sending) and any transfers outside the UK',
          'Right to object to direct marketing, minimum sign-up age and wording of data-subject rights',
          'Whether the saved-freebies list (kept only in the browser of the visitor, page names only) needs any lawful-basis or retention wording',
          'Complaint route to the ICO and the data protection fee position',
        ]}
      />
    </LegalPage>
  );
}
