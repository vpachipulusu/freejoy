# UK affiliate and consumer-trust compliance checklist

Story FREEJ-26. Engineering checklist for the Freejoy public site: UK GDPR and PECR (ICO), the CAP Code and
ASA/CMA affiliate guidance, consumer-protection law, competitions and operator identity.

> **This is not legal advice and nothing on the site may claim compliance because of it.** It is a working
> checklist grounded in official sources, written by engineers for engineers and for the qualified reviewer
> who has to sign the legal pages off. Every legal page stays marked "Draft" until that sign-off exists.
> Research date: 2026-10-29. Re-check each source before launch: several of them were being changed in 2026
> (PECR and the ICO storage-and-access guidance after the Data (Use and Access) Act 2025).
>
> FREEJ-30 (2026-10-30) applied the public-copy findings in section I.2. Its per-item decisions, and the
> ones the owner or reviewer still has to make, are in section I.3.

## Status key

| Status | Meaning |
| --- | --- |
| `Done` | Implemented in Phase 1 on `main`; the evidence (file, test) is named. |
| `Pending Phase 3` | A later story must implement it. The "Acceptance criteria" are worded so they can be copied into that story. Includes Phase 2 items that the catalogue story owns. |
| `Needs professional review` | A legal judgement a qualified reviewer must make. Engineers must not decide it. |
| `Owner action` | Something only the site owner can do (register, buy, supply details, decide). |

## Sources and verification log

"Fetched" means the page was retrieved with WebFetch on 2026-10-29 and the rule text was read from the
returned extract (a summarising model produced the extract, so quote nothing from this document without
re-reading the page). "Search result only" means a search snippet was seen but the page was not read.

| Ref | Source | Result |
| --- | --- | --- |
| S1 | CAP Code section 2, https://www.asa.org.uk/type/non_broadcast/code_section/02.html | Fetched. Rules 2.1 to 2.4. |
| S2 | CAP Code section 3, https://www.asa.org.uk/type/non_broadcast/code_section/03.html | Fetched. Rules 3.1, 3.7, 3.17-3.26, 3.30-3.31, 3.47-3.49. |
| S3 | CAP Code section 8, https://www.asa.org.uk/type/non_broadcast/code_section/08.html | Fetched. Prize draws, closing dates, promoter identity. |
| S4 | CAP Code section 10, https://www.asa.org.uk/type/non_broadcast/code_section/10.html | Fetched. Rules 10.1, 10.5-10.7, 10.12-10.14. The page says the section is under review after the Data (Use and Access) Act 2025. |
| S5 | ASA/CAP advice, Affiliate marketing, https://www.asa.org.uk/advice-online/affiliate-marketing.html | Fetched. Labels, placement, voucher and deals sites. |
| S6 | CMA, Social media endorsements: guidance for brands, https://www.gov.uk/government/publications/reviews-and-social-media-endorsements-guidance-for-businesses-and-brands/social-media-endorsements-guidance-for-brands | Fetched. Commission counts as payment; label as "Ad". The page does not mention affiliate links by name. |
| S7 | ASA/CMA influencers' guide (2023 edition), https://www.asa.org.uk/static/790d2e01-e3f8-4fea-b3c99ef91a9f04dc/Influencerguidance2023v4-FINAL.pdf | Search result only. Unverified, to confirm. |
| S8 | DMCC Act 2024 Schedule 20, https://www.legislation.gov.uk/ukpga/2024/13/schedule/20 | Fetched twice. Paragraph numbers 7, 12, 13, 22 and 23 confirmed against the text. |
| S9 | PECR regulation 22, https://www.legislation.gov.uk/uksi/2003/2426/regulation/22 | Fetched. Shows the version amended on 5 February 2026 (S.I. 2026/82). Regulation 23 not read: unverified, to confirm. |
| S10 | PECR regulation 6, https://www.legislation.gov.uk/uksi/2003/2426/regulation/6 | Fetched, but only paragraphs (1) and (2) were shown. Substituted on 5 February 2026. |
| S11 | PECR Schedule A1, https://www.legislation.gov.uk/uksi/2003/2426/schedule/A1 | Fetched. Paragraphs 2 to 7 (consent and the exceptions). |
| S12 | ICO, Guidance on the use of storage and access technologies, https://ico.org.uk/for-organisations/direct-marketing-and-privacy-and-electronic-communications/guidance-on-the-use-of-storage-and-access-technologies/ and its sub-pages "What are storage and access technologies?" and "What are the exceptions?" | Fetched. The index says the guidance was finalised on 29 April 2026. Sub-pages read as extracts. |
| S13 | ICO, How do we comply with the PECR electronic mail marketing rules?, https://ico.org.uk/for-organisations/direct-marketing-and-privacy-and-electronic-communications/guidance-on-direct-marketing-using-electronic-mail/how-do-we-comply-with-the-pecr-electronic-mail-marketing-rules/ | Fetched. |
| S14 | ICO, Sending direct marketing: choosing your lawful basis, https://ico.org.uk/for-organisations/direct-marketing-and-privacy-and-electronic-communications/sending-direct-marketing-choosing-your-lawful-basis/ | Fetched. Does not cover Article 21 or suppression lists. |
| S15 | UK GDPR Article 7 and Article 21, https://www.legislation.gov.uk/eur/2016/679/article/7 and https://www.legislation.gov.uk/eur/2016/679/article/21 | Fetched. |
| S16 | ICO, What privacy information should we provide?, https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/individual-rights/the-right-to-be-informed/what-privacy-information-should-we-provide/ | Fetched. The page says it is under review after the Data (Use and Access) Act. |
| S17 | ICO, Guide to the data protection fee, https://ico.org.uk/for-organisations/data-protection-fee/data-protection-fee/ | Fetched. Tier amounts as shown on the day. Exemption list not on the page: unverified, to confirm. |
| S18 | Gambling Commission, Free draws and prize competitions, https://www.gamblingcommission.gov.uk/public-and-players/guide/page/free-draws-and-prize-competitions | Fetched. The Gambling Act 2005 itself was not fetched: unverified, to confirm. |
| S19 | Electronic Commerce (EC Directive) Regulations 2002, regulations 6 and 7, https://www.legislation.gov.uk/uksi/2002/2013/regulation/6 and https://www.legislation.gov.uk/uksi/2002/2013/regulation/7 | Fetched. |
| S20 | Company, Limited Liability Partnership and Business (Names and Trading Disclosures) Regulations 2015, https://www.legislation.gov.uk/uksi/2015/17 | Search result only. Unverified, to confirm. The gov.uk guidance page on company disclosures returned 404. |

Fetch failures worth knowing about: `https://www.asa.org.uk/advice-online/recognising-ads-affiliate-marketing.html`
and the ICO page `.../guidance-for-the-use-of-electronic-mail-for-direct-marketing/` both returned 404, so
the older URLs for those topics are dead. Search-engine guidance on `rel="sponsored"` is published on a
domain outside the allowed research list; it is treated here as a product decision, not as a legal source.

---

## A. Recognising advertising and affiliate disclosure

### A1. Marketing must be obviously identifiable

- **Rule:** Marketing communications must be clearly recognisable as marketing (CAP Code 2.1). Marketers must not
  hide commercial intent (2.3), and advertorial must be clearly labelled (2.4). Hidden advertising is also a
  banned practice in DMCC Act Schedule 20 paragraph 12.
- **Source:** S1 rules 2.1, 2.3, 2.4; S8 Schedule 20 paragraph 12.
- **Freejoy:** Phase 1 has no commercial relationship with any brand and no outbound link, so nothing is
  disguised. Once `/go/` links earn commission, listing content that promotes those offers is marketing that
  Freejoy is responsible for.
- **Where:** `src/components/offer/offer-cta.tsx`, `src/components/offer/outbound-link.tsx` (`AffiliateCta`), `src/components/catalogue/offer-card.tsx`, `/go/[offerId]`.
- **Status:** `Built` for criteria 1 and 2 (FREEJ-23: `AffiliateCta` puts the "Ad" text before the button, and that button is the only `/go/` link; FREEJ-60: it does so only when the followed link earns commission, so a `direct` link carries neither the label nor the commission sentence. Wording for owner approval). Criterion 3 (a label on the card) is `Needs professional review`: see decision D-10 in I.3.
- **Acceptance criteria:**
  1. Every control that links to `/go/<offerId>` is preceded, in the DOM and visually, by the word "Ad" (not "Affiliate" alone).
  2. The label is real text in the accessibility tree (not `aria-hidden`, not an image), with at least 4.5:1 contrast.
  3. Where a card or page shows an offer that carries an affiliate link, the label appears on the card and on the offer page, not only in the footer.

### A2. "Affiliate" on its own is not enough; the label must come first

- **Rule:** "Ad" or similar is the clearest label. A standalone "affiliate" label was found insufficient.
  Wording that says the author "may" earn a commission is ambiguous. The label must be obvious before the
  visitor engages, and a disclaimer at the bottom of a page is unlikely to be enough.
- **Source:** S5 sections on acceptable labels and placement; S7 (search result only, unverified, to confirm) for the joint ASA/CMA guidance.
- **Freejoy:** Real offers use the "Ad" label and the sentence "Freejoy is paid a commission by the brand if you take up this offer through this link." (`AffiliateCta`, FREEJ-23). The demo-offer note in `offer-cta.tsx` no longer says "may earn a commission if you qualify" (finding N-09, FREEJ-30). The reviewer still has to confirm the final sentence.
- **Where:** `src/components/offer/outbound-link.tsx`, `src/components/offer/offer-cta.tsx`, `src/app/(public)/affiliate-disclosure/page.tsx`.
- **Status:** `Pending Phase 3` (the `Needs professional review` list below holds the final sentence).
- **Acceptance criteria:**
  1. Label text is "Ad", placed immediately before the call-to-action button, and the sentence next to the button reads along the lines of "Freejoy is paid a commission by the brand if you take up this offer through this link." (reviewer to confirm wording).
  2. The words "may earn" do not appear next to a live affiliate link.
  3. The label is present at every breakpoint, including the compact two-column mobile card.

### A3. A disclosure page alone is not enough

- **Rule:** For voucher and deals sites the advice says marketers should not rely only on "About us", FAQ or
  terms pages; the nature of the content must be obvious to anyone visiting.
- **Source:** S5 section "Is a disclosure page enough?".
- **Freejoy:** `/affiliate-disclosure` exists as a draft and is linked from the offer CTA and footer. It is the
  explanation, not the label.
- **Where:** `src/app/(public)/affiliate-disclosure/page.tsx`, `src/components/site/nav-items.ts` (footer link).
- **Status:** `Done` for the page and its links (draft, worded to be true whether or not a live offer is showing: see audit finding F-01); labelling itself is A1/A2.
- **Evidence:** `home-and-legal-pages.test.tsx`, "affiliate disclosure page" cases.

### A4. Both the business and its affiliate networks are responsible

- **Rule:** Responsibility is not transferred by letting an affiliate publish freely; the brand can be named
  in a ruling. For Freejoy this reads both ways: Freejoy is the publisher and answers for its own copy.
- **Source:** S5 section "Responsibility".
- **Freejoy:** Offer copy is written by Freejoy editors (Phase 2 catalogue). Network-supplied copy must not be pasted in unchecked.
- **Status:** `Pending Phase 3`
- **Acceptance criteria:** The catalogue admin flow requires an editor to read and approve offer title, summary, postage and card fields before an offer is published; imported network text is never published automatically.

### A5. Redirect, `rel` attribute and crawlers

- **Rule:** Not a legal rule. Search engines ask that paid or affiliate links carry `rel="sponsored"`; the owner also wants `nofollow`. The redirect must not hide the destination or the affiliate nature of the link.
- **Source:** Product decision (the search-engine guidance is outside the allowed research domains: unverified, to confirm).
- **Freejoy:** The `/go/[offerId]` redirect and the `rel="sponsored nofollow noopener"` anchor (`src/components/offer/outbound-link.tsx`) are built (FREEJ-23, see `docs/developer/freejoy-affiliate.md`). The public pages on `main` still show only demo offers, which do not link out, so no live offer uses it yet. The affiliate-disclosure page is worded to be true either way: it describes how links work where an offer has one.
- **Where:** `/go/[offerId]` route handler and `src/lib/seo/` (Phase 3 developers), `src/app/robots.ts`.
- **Status:** `Built, awaiting live offers` (route, anchor, robots and tests exist; reviewer sign-off on the Ad label wording is still open under A2).
- **Acceptance criteria:**
  1. Every anchor whose `href` starts with `/go/` has `rel="sponsored nofollow"` (plus `noopener` if `target="_blank"` is used), enforced by one shared link component and a unit test that fails if any `/go/` anchor lacks it.
  2. `/go/` is disallowed in `robots.txt` and absent from the sitemap.
  3. The redirect destination comes only from the stored offer record: no query string, header or request body can influence it. The response carries `Referrer-Policy: strict-origin-when-cross-origin`: the destination learns only the Freejoy origin, never a path or query. This replaces the earlier `no-referrer` criterion by decision (affiliate networks verify the referring domain, and `no-referrer` can leave clicks unattributed). The owner is asked to sign this off.
  4. The redirect sets no cookie, performs no interstitial that hides the destination, and answers a non-live or unknown offer with a normal 404 or a page that says the offer has ended, never a redirect.

### A6. Sponsored or paid placements

- **Rule:** Any placement a brand pays for beyond commission is advertising and must be labelled as such (CAP 2.1; S6 says any reward including commission counts as payment).
- **Source:** S1 rule 2.1; S6.
- **Freejoy:** Paid listings exist as a data model and a label (FREEJ-53): an offer whose commercial basis is `SPONSORED` (the retailer pays, or gives Freejoy any other benefit, for the listing or its placement) carries a text "Sponsored" badge on its card and page (role `note`, named "Sponsored listing", visible at 320px) and a sentence beside the button. Links that earn commission through a network keep the separate "Ad" label. A sponsored offer with an affiliate link shows both. The label cannot be hidden by any setting, and a featured offer can still be sponsored. `/affiliate-disclosure` has a "Sponsored listings" section. Whether any sponsorship is actually sold, and the exact wording, are for the reviewer.
- **Status:** `Needs professional review`

---

## B. "Free" claims and the five offer types

### B1. What may be called free

- **Rule:** Do not describe something as "free" if the consumer pays more than the unavoidable cost of
  responding and of collecting or paying for delivery (CAP Code 3.23). The commitment needed to take up a free
  offer must be made clear (3.24). A package item that is already in the package price is not "free" (3.25).
  "Free trial" must not describe satisfaction-or-money-back offers or offers needing a non-refundable
  purchase (3.26). Describing a product as free when the consumer pays more than the unavoidable cost is a banned
  practice in DMCC Act Schedule 20 paragraph 23.
- **Source:** S2 rules 3.23 to 3.26; S8 Schedule 20 paragraph 23.
- **Freejoy rule (stricter, owner-set):** never say "completely free" for anything with postage, a card, a subscription or an entry condition.
- **Status:** `Done` for the body copy of the Phase 1 sample listings (no "completely free" anywhere: grep of `src/components`, `src/app/(public)`, `src/lib/catalogue`). Chip, CTA and tagline wording was changed in FREEJ-30 (findings N-01 to N-04, decisions D-01 to D-04 in I.3); the owner is asked to confirm the strict reading.

### B2. Free sample with postage

- **Rule:** CAP 3.23 allows the unavoidable cost of delivery, so a sample with postage can be called a free sample, but the postage must be stated clearly and no packing, handling or inflated delivery charge may be added.
- **Source:** S2 rule 3.23, 3.20 (optional delivery charges must be stated).
- **Freejoy:** `postageText()` shows "Free", "£x.xx" or "Check terms"; the offer page shows a POSTAGE chip and a "Cost & postage" row. Postage "Check terms" is honest but must be resolved to a figure or "Free" before an offer goes live.
- **Where:** `src/components/offer/offer-format.ts`, `important-bits.tsx`, `offer-trust-panel.tsx`.
- **Status:** `Done` for display. Call-to-action: the line itself is `Done` (FREEJ-30: `ctaConditions()` renders "Postage: £2.99." after the button, from the offer's own postage field); the publish-time rule is `Pending Phase 3`.
- **Acceptance criteria:** A free-sample CTA never appears without the postage amount rendered inside the same `<section>` as the CTA, after the button in DOM order, at every breakpoint ("Get this sample. Postage £2.99."); an offer with `postage.kind = 'check_terms'` cannot be published live.

### B3. Free trial that converts to a paid plan

- **Rule:** The commitment must be clear (3.24): card requirement, trial length, the price charged afterwards and how to cancel. Misleading omission is covered by 3.1.
- **Source:** S2 rules 3.1, 3.24, 3.26.
- **Freejoy:** `important-bits.tsx` shows "A payment card is required to start", "Trial terms" and "Cancellation" from `subscriptionTerms`; the card-required chip is in the trust panel. The CTA label "Start the free trial" sits above those details.
- **Where:** `src/components/offer/important-bits.tsx`, `offer-trust-panel.tsx`, `offer-format.ts:25,33`.
- **Status:** `Done` for the detail block. CTA: FREEJ-30 renamed the button "Start the trial" and `ctaConditions()` renders "A payment card is required to start." after it when `requiresCard` is true. The trial length, post-trial price and cancellation route are still only in the detail block, so the rest of the acceptance criteria below is `Pending Phase 3`.
- **Acceptance criteria:** A trial offer cannot be published without `requiresCard` decided and `subscriptionTerms` containing the trial length, the post-trial price and the cancellation route. The CTA is followed by one line generated from those fields ("Card required. £X a month after day N unless you cancel.").
- **Also:** The new subscription-contract rules in the DMCC Act are the brand's duty, not Freejoy's. Whether and when they change what a listing must say: `Needs professional review` (not researched here).

### B4. Cashback

- **Rule:** Not free: the buyer pays first and is refunded later. Must not be labelled or badged as a free sample (CAP 3.1, 3.23).
- **Source:** S2.
- **Freejoy:** `OFFER_TYPE_LABELS` keeps `cashback` separate from `free_sample`; fixtures state "This is cashback, not a free bag" and the claim steps say "you pay first and are refunded later".
- **Where:** `src/lib/catalogue/types.ts:128`, `src/components/catalogue/offer-type-badge.tsx`, `fixtures.ts:381`.
- **Status:** `Done`
- **Evidence:** `src/__tests__/unit/lib/catalogue/*` and the badge component comment ("never badged as a free sample").

### B5. Vouchers

- **Rule:** A voucher reduces a price; it must not be called free. State minimum spend, covered products and expiry.
- **Source:** S2 rules 3.17 to 3.23.
- **Freejoy:** Fixtures say "It is a discount rather than a free product" and the claim steps list minimum spend and expiry.
- **Status:** `Done`

### B6. Competitions

- **Rule:** An entry is not a prize. Never imply a visitor has won, or will win (CAP 8.19 to 8.21; Schedule 20 paragraph 22). Free-entry requirements: section G.
- **Source:** S3; S8 paragraph 22.
- **Freejoy:** CTA reads "Enter this competition"; steps say "entering is no guarantee".
- **Status:** `Done` for Phase 1 copy; `Pending Phase 3` for alert and email wording (acceptance criteria D6).

### B7. Site-level "free" and "zero spend" slogans

- **Rule:** The "free" rules apply to the site's own marketing, not only to listings. A slogan that says every offer costs nothing contradicts the cashback, voucher, postage and trial offers listed beside it (CAP 3.1, 3.23).
- **Source:** S2.
- **Freejoy:** The announcement bar, hero eyebrow and footer tagline claimed "zero spend", "for £0" and "no spend" (findings N-01 to N-03). Reworded in FREEJ-30 (decisions D-01 to D-03).
- **Status:** `Done` for the change. `Needs professional review` for the replacement wording (J3).

### B8. Prices, "from" and "up to"

- **Rule:** Prices must include non-optional charges, optional delivery charges must be stated, and "up to"/"from" must not exaggerate (CAP 3.17 to 3.22). Regulation 6(2) of the E-Commerce Regulations asks that prices be clear, including tax and delivery.
- **Source:** S2; S19 regulation 6(2).
- **Freejoy:** Postage is stated per offer. No "up to" or "worth £x" claims exist in Phase 1.
- **Status:** `Done`. Phase 2/3 note: do not add "worth £x" or "save up to" values without evidence held on file (CAP 3.7).

---

## C. Urgency, counts, testimonials and verification dates

### C1. No manufactured urgency

- **Rule:** Do not claim a product or its terms are available only for a limited time when that is false (CAP 3.30; Schedule 20 paragraph 7). Do not mislead about availability (3.31).
- **Source:** S2 rules 3.30, 3.31; S8 paragraph 7.
- **Freejoy:** There are no countdowns or "only x left" claims. Editorial freshness labels "Ends soon" and "Limited" exist in `FRESHNESS_LABELS` (finding N-05). Since FREEJ-30, `displayFreshness()` (`src/lib/catalogue/freshness.ts`) hides them for every real (API) offer; only a demo fixture, which carries the demo badge, still shows them.
- **Status:** `Done` for copy and for the display rule (unit-tested); `Pending Phase 3` for the data model.
- **Acceptance criteria:** "Ends soon" is computed from the real `endAt` against the real date (suggested threshold: within seven days) and cannot be set by an editor. "Limited" can only be shown when the provider's own terms state a cap, and the editor records that wording. No label implies an unverified deadline. "While stocks last" is shown as the provider's wording, never as urgency.

### C2. No fake counts or popularity claims

- **Rule:** Objective claims need documentary evidence held before publication (CAP 3.7). "Popular" is an objective claim.
- **Source:** S2 rule 3.7.
- **Freejoy:** The homepage trust strip has no digits (unit test). "Popular" is a freshness label on three sample offers (finding N-05); `displayFreshness()` hides it for real (API) offers (FREEJ-30).
- **Status:** `Done` for counts and for keeping the label off real offers. `Pending Phase 3`: remove the `popular` value or back it with a recorded, explained metric; never show visitor, click or claim counts unless the number is real and its source is shown.
- **Evidence:** `home-and-legal-pages.test.tsx`, "describes freshness as regular and uses no counts in the trust strip".

### C3. No testimonials or reviews

- **Rule:** Testimonials need proof they are genuine and contact details (CAP 3.47). Fake or hidden-incentive reviews are banned (Schedule 20 paragraph 13).
- **Source:** S2 rule 3.47; S8 paragraph 13.
- **Freejoy:** None shown (grep for testimonial, review, rating, stars across the public components found nothing).
- **Status:** `Done`
- **Acceptance criteria (Phase 3 SEO):** JSON-LD must not include `Review`, `AggregateRating` or star values.

### C4. Verification dates

- **Rule:** A "last checked" date is an objective claim (CAP 3.7, 3.1). It must be the date a person actually checked the brand's page.
- **Source:** S2.
- **Freejoy:** For a demo (fixture) offer, `OfferTrustPanel` now reads "Sample date <date>" with "Sample listing: Freejoy has not checked this offer.", an info icon instead of a tick and the region name "Offer details" (finding N-11, FREEJ-30). "Last checked <date>" appears only for a real offer, from the editor's `last_verified_at`; the API mapper drops an offer that has none.
- **Where:** `src/components/offer/offer-trust-panel.tsx`, `src/lib/catalogue/fixtures.ts`.
- **Status:** `Done` for the display; `Pending Phase 3` for the editor-action rule below.
- **Acceptance criteria:** `lastVerifiedAt` is set only by an editor action that records who and when; it is never defaulted to the creation time or bumped by an import; the UI shows nothing (or "not recently checked") when it is empty or older than a threshold the owner sets. JSON-LD `dateModified` uses the same field.

### C5. Compliance claims

- **Rule:** The product rule is that no compliance claim appears on the site without evidence. There is no "GDPR compliant", "ASA approved", "verified" or "ICO registered" wording.
- **Status:** `Done`
- **Evidence:** New unit tests ("makes no compliance, registration or approval claims", every legal page and the contact page) in `home-and-legal-pages.test.tsx`; the trust-strip and offer pages carry only intentions.

---

## D. Email alerts: PECR regulation 22, UK GDPR and CAP section 10

### D1. Consent is required; the soft opt-in does not apply

- **Rule:** Marketing email to individual subscribers needs the recipient's prior consent unless the soft opt-in applies: details obtained during a sale or negotiation for a sale, marketing only of the sender's own similar products, and an opt-out at collection and in every message (PECR regulation 22(2) and (3)). The charity soft opt-in is separate and not relevant. CAP 10.6 repeats this.
- **Source:** S9 regulation 22(2), (3); S13; S4 rule 10.6. S13 says a free-trial sign-up or a quote request can count as a negotiation for a sale; browsing, or asking for information that is not about buying, does not.
- **Freejoy:** Freejoy sells nothing. A visitor who signs up for freebie alerts is not buying or negotiating to buy anything from Freejoy, so the soft opt-in is not available. Alerts rely on consent alone.
- **Status:** `Needs professional review` (confirm the soft-opt-in reasoning in writing). Design rule: never use the soft opt-in, never email an address that has not completed D3.

### D2. Consent standard and wording

- **Rule:** Consent is a clear positive action; pre-ticked boxes and silence do not count. It must be separate from terms, specific to email marketing, name the sender, and withdrawal must be as easy as giving consent (UK GDPR Article 7(2), (3); ICO email guidance; CAP 10.12).
- **Source:** S13; S15 Article 7; S4 rule 10.12.
- **Freejoy:** The alerts form has an unticked consent checkbox, validated with Zod, reading "I agree to receive freebie emails from Freejoy. I can unsubscribe at any time." The homepage band has no checkbox: the visitor reaches the alerts page, which does.
- **Where:** `src/components/alerts/alerts-form.tsx:29,191`, `src/components/home/newsletter-band.tsx`.
- **Status:** `Done` for the unticked box and the preview state ("nothing was saved"). `Pending Phase 3` for the live wording.
- **Acceptance criteria:**
  1. The checkbox is unticked by default and its label names the sender (the legal operator once known), says the emails are about freebies and offers, and says how to withdraw.
  2. The consent statement is stored as a versioned string with each record (D4).
  3. The form never says "subscribed" before the double opt-in is confirmed (D3).

### D3. Double opt-in (product decision)

- **Rule:** Not required by PECR or the ICO guidance fetched (the guidance asks for records that show who consented, when and how). Freejoy chooses double opt-in so the record cannot be created by someone typing another person's address.
- **Source:** S13 (records of consent); product decision.
- **Status:** `Pending Phase 3`
- **Acceptance criteria:**
  1. Submitting the form creates a pending record and sends one confirmation email; no alert email is ever sent to a pending address.
  2. The confirmation link carries a single-use random token that expires (suggested 48 hours) and is stored hashed.
  3. A confirmation click moves the record to confirmed and stores the confirmation time; a second click is harmless.
  4. Pending records that are never confirmed are deleted after the token expiry plus a short grace period, and the privacy notice says so.
  5. The confirmation email contains the sender identity and the sign-up details, and is the only email sent before confirmation.
  6. The email address is submitted by POST (server action), never in a URL, so it cannot reach hosting request logs, browser history or Sentry transaction and breadcrumb URLs (findings N-07, N-12). **Built (FREEJ-24, FREEJ-31).** The homepage newsletter band is a plain link to `/alerts`, `/alerts` ignores `?email=` and never prefills, the form posts through a server action, and a code audit in FREEJ-31 found no link, redirect, server action or auth flow that puts an address in a page address. As defence in depth, `src/lib/sentry/scrub-event.ts` (`beforeSend` and `beforeSendTransaction` in the client, server and edge configs) filters, in every URL, query string, transaction and span name, span attribute, breadcrumb `to`, `from` and `url`, and `referer` header: a `token` parameter, any parameter whose name contains `email` (in the query or a `#fragment`, also percent-encoded), any parameter whose value is an email address, and any other address-shaped token (path segment, userinfo, nameless query); the backend scrubber drops query strings and replaces any address in the request path and in every string of breadcrumbs, contexts, extra and headers. These are unit-tested on constructed events only; they have not been checked against a live Sentry project. **Not covered:** hosting request logs and browser history keep whatever is in the address, and the site search box (`/freebies?q=`) and the admin user search (`/admin/users?q=`) are GET forms, so an address typed into either lands in the address there (Sentry is scrubbed of it). Reviewer to confirm that is acceptable; the privacy notice says so for the search box.
  7. Subscribe is rate limited per IP and has a per-address cooldown, and returns the same response whether or not the address is already known (no enumeration).
  8. The confirmation token is generated with a CSPRNG, is at least 128 bits, is stored hashed, is compared by hash with a constant-time comparison and is invalidated on use.
  9. The confirmation link survives mail-scanner prefetch: the link opens a GET landing page that changes nothing, and a POST from that page confirms.
  10. Subscribe, confirm and unsubscribe are reachable only through server actions or POST requests.

- **Implementation note (FREEJ-24, [backend-subscribers.md](../developer/backend-subscribers.md)):** criteria 1 to 10
  are built, with these differences for the reviewer: the token expires after 24 hours (not 48) and is
  an HMAC-signed value of at least 128 bits of nonce whose SHA-256 is stored; unconfirmed records are deleted after
  30 days and unsubscribed records after 12 months by a manually run command (the story's periods; criterion 4 and D4
  suggested shorter, and D4 an indefinite suppression list); the consent wording is stored in full rather than as a
  version id; D5 criterion 5 (re-subscribing revokes an old unsubscribe token) is not built. Status stays `Pending
  Phase 3` until a provider is live and the owner has reviewed the wording.

### D4. Records of consent

- **Rule:** Keep records showing who consented, when and how (ICO email guidance; UK GDPR Article 7(1): be able to show consent).
- **Source:** S13; S15 Article 7(1).
- **Status:** `Pending Phase 3`
- **Acceptance criteria:** Each subscriber record stores: email, the categories and frequency chosen, the consent statement version, the date and time of the request, the date and time of confirmation, and the source route (the path only, never a full URL or query string). Whether to store an IP address or user agent as extra proof is a judgement for `Needs professional review`; do not store them by default. Unsubscribed addresses stay on a suppression list holding only a hash or the address, so a later import cannot re-add them (the ICO advises keeping a do-not-contact list).

### D5. Unsubscribe in every email

- **Rule:** Every marketing message must offer a simple opt-out; requiring a login or account is not simple; the sender's identity must not be hidden and a valid contact address must be given (PECR regulation 22(3)(c); CAP 10.6, 10.7).
- **Source:** S9; S13; S4.
- **Where:** The alerts form promises it ("Once alerts launch, every email will carry a one-click unsubscribe link", `alerts-form.tsx:254`).
- **Status:** `Pending Phase 3`
- **Acceptance criteria:**
  1. Every alert email has a visible one-click unsubscribe link that works without sign-in, and also sends the `List-Unsubscribe` and `List-Unsubscribe-Post` headers (industry practice; no official-domain source was researched: unverified, to confirm).
  2. Unsubscribing takes effect immediately for the next send, and the confirmation page does not try to re-sell the alerts.
  3. The email footer shows the sender name, a postal address and a reply address (S19 regulation 7(b); S4 rule 10.7).
  4. The unsubscribe endpoint is rate limited and idempotent, and the token reveals nothing about the address.
  5. There is one unsubscribe token per subscriber record; re-subscribing revokes any earlier unsubscribe token.

### D6. Email content: recognisable, labelled, honest

- **Rule:** Unsolicited marketing email must be recognisable as marketing without being opened (CAP 2.2). Commercial communications must be identifiable and identify promotional offers and competitions with easy-to-find conditions (E-Commerce Regulations regulation 7).
- **Source:** S1 rule 2.2; S19 regulation 7.
- **Status:** `Pending Phase 3`
- **Acceptance criteria:** Subject lines contain no urgency or fake counts and no "completely free"; affiliate offers in an email carry the same "Ad" label as on the site (A1/A2) before the link; the email states postage, card and subscription conditions or links to the page that does.

### D7. Right to object and withdrawal

- **Rule:** The right to object to direct marketing is absolute and must be brought to the person's attention clearly and separately at the latest on first contact (UK GDPR Article 21(2) to (4)). CAP 10.13 says the same where legitimate interests are relied on.
- **Source:** S15 Article 21; S4 rule 10.13.
- **Status:** `Needs professional review` (whether the consent basis makes a separate Article 21 notice necessary, and the wording). The privacy notice draft already lists the rights.

### D8. Children

- **Rule:** The age at which a child can consent to an online service, and any under-18 limits on offers, are a legal judgement; some fixtures use "aged 16 or over" and "aged 18 or over".
- **Source:** UK GDPR Article 8 (not fetched: unverified, to confirm).
- **Status:** `Needs professional review`
- **Acceptance criteria:** The alerts form states the minimum age for sign-up once the reviewer sets it.

### D9. Transactional email versus marketing

- **Rule:** A confirmation or unsubscribe acknowledgement is a service message and must not carry promotion.
- **Source:** S13 (the ICO email guidance on direct marketing; whether service messages sit outside it is for the reviewer to confirm).
- **Status:** `Pending Phase 3`
- **Acceptance criteria:** The double-opt-in confirmation and unsubscribe acknowledgement contain no offers, banners or affiliate links.

---

## E. Cookies and storage: PECR regulation 6

### E1. The rule as it stands in 2026

- **Rule:** A person must not store information on, or gain access to information stored on, a user's device unless an exception applies (regulation 6(1), as substituted on 5 February 2026). Schedule A1 gives the exceptions: consent after clear and comprehensive information (para 2), transmission of a communication (3), strictly necessary for a service the user requested (4), statistical purposes with a simple means of objecting (5), website appearance with a simple means of objecting (6), emergency assistance (7). The ICO says web storage (localStorage and sessionStorage), pixels, scripts and fingerprinting are covered, and that the exceptions are purpose-specific.
- **Source:** S10; S11; S12.
- **Status:** `Needs professional review` for the final decision. The technical audit is below.

### E2. Audit of what the site stores today (code review, 2026-10-29)

Method: searched `nextjs-frontend/src` and `package.json` for `cookie`, `cookies(`, `document.cookie`, `localStorage`, `sessionStorage`, `indexedDB`, `gtag`, `googletagmanager`, `plausible`, `posthog`, `@vercel/analytics`, `speed-insights`, `hotjar`, `clarity`, `mixpanel`, `fbq`, `next/script` and `<script`; read `instrumentation-client.ts`, `next.config.ts`, the root layout and the theme provider; read the installed `next-themes` 0.4.6 and `@sentry/browser` 10.76.2 code for what they write. The code audit was then checked at runtime on 2026-10-29 against a local production build (`npm run build`, `next start`, no `NEXT_PUBLIC_SENTRY_DSN`, no Firebase or backend variables): the home page, `/freebies`, an offer page, `/alerts`, `/about`, `/privacy`, `/cookies`, `/terms`, `/contact` and `/affiliate-disclosure` returned no `Set-Cookie` header; in the browser `document.cookie` was empty, `localStorage`, `sessionStorage` and IndexedDB were empty, and every network request on `/`, an offer page and `/alerts` went to the page's own host. That check does not cover a deployment with real environment variables (Sentry DSN, Firebase), so repeat it there before launch (action K6).

| Item | Where | Public catalogue pages? | PECR position (for the reviewer) |
| --- | --- | --- | --- |
| `session` cookie (httpOnly, `secure` in production, SameSite=Lax, 5 days) | `src/app/actions/auth-actions.ts` (`setSessionCookie`), name in `src/lib/api/server.ts:20` | No. Set only when a person signs in. | Strictly necessary for a service the user asked for (Schedule A1 para 4: authentication). No consent needed; named on the cookie notice ("called `session`", kept for up to five days). |
| `email-verified` cookie | Deleted by `setSessionCookie` ("any left over from an older deployment") | No | Not set any more. |
| Third-party analytics, advertising pixels, tag managers, social widgets | No match anywhere in `src` or `package.json` | No | None to consent to. |
| Theme preference in `localStorage` (key `theme`) | `src/components/providers/theme-provider.tsx` (next-themes) | Nothing is written on public pages: next-themes writes the key only inside its `setTheme` function, and no component calls `setTheme`; there is no theme toggle. But the provider reads the `theme` key on every page load. | A read is "access" under PECR regulation 6(1). Reviewer to confirm the appearance exception or strictly-necessary covers reading a key that is never written (section J item 9). If a toggle is added: appearance exception (para 6) with a simple means of objecting, or consent. |
| Firebase client SDK state | `src/lib/firebase/client.ts`, imported by the sign-in, sign-up, password-reset and dashboard components | Not imported by the public layout or providers. Initialised on the sign-in pages (`/login`, `/signup`), before sign-in (confirm in browser) | Opening a sign-in page prepares Firebase Auth storage (IndexedDB) even if the visitor never signs in (confirm in browser, K6). Strictly necessary for sign-in, same reasoning as the session cookie; the cookie notice says so. |
| Saved freebies list in `localStorage` (key `freejoy.saved.v1`, FREEJ-28) | `src/lib/saved/saved-store.ts` (the only code that touches browser storage) | Written only when the visitor presses a heart. Holds offer slugs only, up to 200, nothing else; kept until the visitor removes them or clears browser data. The Saved page sends the slugs to a server action to look the offers up; nothing is kept, but the lookups appear in ordinary server logs. | Our reading: strictly necessary for a service the user requested (Schedule A1 para 4), no consent needed. **To be confirmed** in review; listed in the draft cookie notice and mentioned in the draft privacy notice (both still drafts). |
| Sentry browser SDK | `src/instrumentation-client.ts` (`enabled` only when `NEXT_PUBLIC_SENTRY_DSN` is set; replay sample rates 0) | Runs on every page when enabled | Writes no cookie. The only `sessionStorage` use is `linkPreviousTrace: "session-storage"`; the installed default is `"in-memory"` (`@sentry/browser` `browserTracingIntegration.js:33`), so nothing is written. This is a UK GDPR matter instead: the SDK sends error and performance events, which normally include the IP address, to Sentry, and page addresses in transaction, span and breadcrumb data are scrubbed of `token`, `email` parameters and email-address values before sending (`src/lib/sentry/scrub-event.ts`, FREEJ-31; unit-tested on constructed events only, not checked against a live Sentry project). No page puts an address in its address since FREEJ-24 (N-07, N-12), but the search box can if a visitor types one. See D3.6 and F4. |
| `sentry-trace` and `baggage` headers on same-origin requests | Sentry tracing | Yes when enabled | Not storage or access to the device; no identifier is stored. Reviewer to confirm. |
| Fonts | `next/font` (self-hosted at build) | Yes | No request to a font provider. |
| Vercel hosting request logs | Platform | Yes | Server-side, not PECR regulation 6, but personal data (IP address). See F4. |
| Winston application logs | `src/lib/logger.ts`: daily rotating files in `logs/` on the server (app log kept 7 days in development and 14 in production, error log 30 days); emails and tokens are redacted at logger level | Server-side only | Not PECR regulation 6. Personal data may still reach them despite redaction; list in the privacy notice. See F4. |

### E3. Is a cookie banner needed?

- **Conclusion for now:** Not on the public pages. The audit finds nothing written to a visitor's device for a person who only browses (the `theme` key is read but never written: see the next-themes row), no analytics, and no advertising. The cookie notice therefore says the site intends to set no non-essential cookies and shows no banner. This stays an engineering conclusion, not a compliance claim, until the reviewer signs the cookie notice off.
- **Status:** `Done` for the audit and the draft notice (`src/app/(public)/cookies/page.tsx`); `Needs professional review` for sign-off.
- **Re-open this decision when any of these change:** an analytics tool is added (even a cookieless one: check the statistical-purposes exception, which needs clear information and a simple means of objecting, S11 para 5); an embedded video or map; a theme toggle that writes to storage; any affiliate network pixel; any advertising; Sentry Replay or `linkPreviousTrace: "session-storage"`.

### E4. Phase 3 constraints on storage

- **Rule:** As E1: any storage or access needs consent or an exception (PECR regulation 6, Schedule A1).
- **Status:** `Pending Phase 3`
- **Acceptance criteria:**
  1. The `/go/` redirect and click-event recording set no cookie, write no `localStorage`/`sessionStorage`, and read no identifier from the device. The click-event table keeps no IP address, cookie id or user id (owner design decision; see F3).
  2. Any added analytics or affiliate tracking script is listed on `/cookies` before release, goes through a consent mechanism unless the reviewer confirms an exception, and does not load before consent.
  3. A unit test fails if `document.cookie`, `localStorage`, `sessionStorage` or `indexedDB` appears in `src/components`, `src/app/(public)` or `src/lib/catalogue` without an allow-list entry naming the cookie-notice row it belongs to. The same test also covers `src/app/(auth)` and `src/lib/firebase`, where the allowed entries are the sign-in storage items.

---

## F. UK GDPR: lawful basis and privacy notice

### F1. Lawful basis per purpose

- **Rule:** Every purpose needs a lawful basis; where PECR requires consent, consent must be the basis (ICO lawful-basis page). Individual subscribers: consent. Site security and abuse prevention: legitimate interests, with a balancing test recorded.
- **Source:** S14.
- **Freejoy (proposed, for the reviewer):**

| Purpose | Data | Proposed basis |
| --- | --- | --- |
| Send freebie alerts | Email, categories, frequency, consent record | Consent (UK GDPR Article 6(1)(a), PECR regulation 22) |
| Confirm a sign-up (double opt-in) | Email, token | Consent (the request being confirmed) |
| Suppress after unsubscribe | Email or hash | Legal obligation / legitimate interests (reviewer to choose) |
| Count outbound clicks (offer id, time) | No IP, cookie id or user id | Likely not personal data; if the reviewer disagrees, legitimate interests |
| Error monitoring and security logs | Technical data including IP | Legitimate interests |
| Accounts: email, name, sign-in times, IP address and browser of each sign-in, two-factor settings | Held by the backend (`users`, `login_history`) | Contract or legitimate interests (reviewer to choose); retention to be set |
| Contact form replies | Name, email, message | Legitimate interests or consent (the form is off today) |

- **Status:** `Needs professional review`

### F2. Privacy notice contents

- **Rule:** The notice must give: the controller's name and contact details; each purpose; lawful basis; legitimate interests relied on; recipients; transfers outside the UK and safeguards; retention or the criteria; the rights that match the basis; the right to withdraw consent; the right to complain to the ICO; whether providing data is required; any automated decision-making. The right to object to marketing must be shown clearly and separately.
- **Source:** S16; S15 Article 21(4).
- **Freejoy:** `/privacy` is a draft with a reviewer checklist and no invented facts. It now also discloses what is true today (hosting logs, `?email=` in the URL from the homepage band, possible error reporting) and describes the planned alerts as planned. The outbound click record (what it holds, that it holds no IP address or cookie identifier, and that rows are deleted after 13 months / 395 days by `commands/prune_click_events.py`) is described as live behaviour.
- **Where:** `src/app/(public)/privacy/page.tsx`.
- **Status:** `Needs professional review`

### F3. Click events and data minimisation

- **Rule:** Collect only what is needed (UK GDPR data-minimisation principle). The design deliberately keeps IP address, cookie id and user id out of the click-event table.
- **Source:** Owner design decision; UK GDPR principles (Article 5, not separately fetched: unverified, to confirm).
- **Status:** `Pending Phase 3`
- **Acceptance criteria:** The click-event insert takes only offer id, a timestamp and (optionally) the referring route; a backend test asserts the table has no IP, cookie or user-id column. The request is not logged with IP at application level beyond what the platform does by default, and the privacy notice mentions that platform logs exist (F4). An offer id plus a second-precision timestamp can be joined with hosting request logs to single out a visitor: truncate the timestamp to the hour, or list the join risk for the reviewer.

### F4. Processors, logs and transfers

- **Rule:** The notice must name recipients and describe transfers outside the UK.
- **Freejoy:** Hosting provider logs (IP address, user agent), server-side Winston application logs (7/14/30-day rotation, emails and tokens redacted at logger level) and, if the DSN is set, Sentry events (IP, browser, page and URL, with `token`, `email` and email-address query values removed before sending) are processed today on any deployed instance. The email provider for Phase 3 is not chosen.
- **Status:** `Needs professional review`; `Owner action` K4 (choose and list providers, check data-processing terms and transfer mechanism).

### F5. Data subject requests and retention

- **Rule:** People can ask for access, correction and deletion; the notice must say how and for how long data is kept.
- **Source:** S16; S15 Article 21 for objection. Retention periods are set by the owner and the reviewer (K8), not by this checklist.
- **Status:** `Pending Phase 3` for the mechanism. Acceptance criteria: a documented route (the contact page, once live) handles access and deletion requests; subscriber records and tokens have retention periods set by the owner and enforced by a scheduled job.

### F6. Contact form

- **Rule:** The form is switched off, collects nothing, and the contact page says so.
- **Status:** `Done`
- **Evidence:** `utility-pages.test.tsx` "keeps the form disabled and sends nothing".

---

## G. Competitions and prize draws

Freejoy lists competitions run by brands; it does not run them. The promoter carries the CAP section 8 and
Gambling Act duties. Freejoy still must not list something it can see is unlawful or misleading, and must
describe entry conditions fairly.

### G1. Freejoy runs no draws of its own

- **Rule:** A draw where people pay and names are drawn is a lottery needing a licence. A free draw or prize competition needs no licence if it meets the Gambling Act 2005 requirements: a free entry route that is no more costly or less convenient than the paid route, promoted equally and treated equally; or a prize competition where skill is a real barrier (S18 says multiple-choice questions rarely qualify).
- **Source:** S18; the Gambling Act 2005 text itself was not fetched (unverified, to confirm).
- **Freejoy:** Phase 1 has none; fixtures describe only brand-run draws ("Winners are chosen by the organiser").
- **Status:** `Needs professional review` before Freejoy ever runs its own giveaway. Rule until then: no Freejoy-run competition.

### G2. Listing criteria for third-party competitions

- **Rule:** CAP section 8 asks for: the promoter's name and address (8.17.9); significant conditions (8.17); a prominent closing date (8.17.4); entry limits (8.28.1); how and when winners are told (8.28.4); a free-entry route explained clearly where one exists (8.17.2); no claim that someone has won (8.19 to 8.21).
- **Source:** S3.
- **Status:** `Pending Phase 3`
- **Acceptance criteria:** A competition offer cannot be published unless the editor has recorded: promoter name, closing date, minimum age, any purchase or payment needed to enter, and the free-entry route if there is a paid one. If a purchase or payment is needed, the offer is not typed as a competition without that stated in "Cost & postage". An offer past its closing date is shown as ended and has no live CTA (already handled by `isLive`/`ended`).

### G3. No implied winning

- **Rule:** Never say or imply a visitor has won or will win.
- **Source:** S3 rules 8.19 to 8.21; S8 paragraph 22.
- **Status:** `Done` (CTA "Enter this competition"; steps "entering is no guarantee").

---

## H. Operator identity, ICO fee, footer and contact page

### H1. Who runs Freejoy must be visible

- **Rule:** Information society service providers must make their name, geographic address and contact details including an email address easily, directly and permanently accessible; a VAT number if VAT-liable (E-Commerce Regulations regulation 6(1)). A UK company must show its registered name, place of registration, registered number and registered office on its website; a sole trader or partnership using a business name has related duties.
- **Source:** S19 regulation 6(1)(a) to (c), (g); S20 (search result only, unverified, to confirm).
- **Freejoy:** The operator is not decided or registered. No page invents one. The contact page now has a clearly marked "Who runs Freejoy" placeholder and the footer is one line with policy links.
- **Where:** `src/app/(public)/contact/page.tsx` (marked placeholder: "They have not been added yet"); footer `src/components/site/site-footer.tsx` (finding N-14; decision D-14 in I.3: kept without operator details on purpose).
- **Status:** `Owner action` (K1, K2) then `Pending Phase 3` for the footer. Until the owner supplies the details no public page shows any, and none is invented.
- **Acceptance criteria:** Before any live offer is published, the footer shows (or links to the contact page showing) the legal name, trading name if different, registered number and registered office or business address for a company, a working email address, and the VAT number if registered. The values come from site configuration, not copy typed into components.

### H2. ICO data protection fee

- **Rule:** Organisations that process personal data generally must pay the data protection fee unless exempt. The page shows three tiers (as displayed 2026-10-29): £52 for turnover up to £632,000 or up to 10 staff, £78 for up to £36 million or 250 staff, and £3,763 above that.
- **Source:** S17. The exemption list is on a separate ICO page that was not fetched: unverified, to confirm. Use the ICO's own self-assessment.
- **Status:** `Owner action` K3. Freejoy must not write "registered with the ICO" anywhere until the registration exists and the number is on file.

### H3. Complaints and redress

- **Rule:** The privacy notice must give the right to complain to the ICO (S16).
- **Status:** `Needs professional review` (wording is in the reviewer checklist on `/privacy`).

---

## I. Audit of the existing Phase 1 copy

Severity: High = likely to mislead or breaks a stated product rule; Medium = wording that overpromises or
will become wrong in Phase 3; Low = minor. "Fixed" means changed in this story's branch; "Open" means the file
is owned by someone else and the coordinator should apply the suggested wording.

### I.1 Findings in files owned by this story (fixed)

| ID | Where | Problem | Rule | Result |
| --- | --- | --- | --- | --- |
| F-01 | `src/app/(public)/affiliate-disclosure/page.tsx:19-20` (before) | "Some links on Freejoy are affiliate links" in the present tense, though none exists; "may earn" is the ambiguous wording | S5; CAP 3.1 | Fixed, then reworded in FREEJ-23. The page first described the redirect, `rel` marking and "Ad" label as planned and said no commission could be earned today and that there were no outbound links. Because the redirect is now built and the page must stay true whether or not a live offer is showing, it now says how links work where an offer has one (the `/go/` address, the paid-link marking, the "Ad" label and commission sentence, that the brand pays the commission, and what a click records: no IP address, no cookie, no identifier), with no "planned" or "no outbound links" wording. It stays a draft for legal review. A unit test fails if those phrases return. |
| F-02 | same, lines 24-26 | "never changes the terms ... editorial choice made independently" stated as a fact | CAP 3.7 (evidence) | Fixed. Reworded as intentions, to be confirmed by the reviewer. |
| F-03 | `src/app/(public)/privacy/page.tsx:19-20` | "does not save any information" ignores hosting logs, error reporting and the e-mail address carried in the URL by the homepage band | UK GDPR transparency; S16 | Fixed. Describes what is true today and marks the rest planned. |
| F-04 | `src/app/(public)/privacy/page.tsx:22-27` | No mention of the planned double opt-in, consent record, click events or processors | S16; F1 to F4 | Fixed. Added as planned, plus reviewer checklist items. |
| F-05 | `src/app/(public)/cookies/page.tsx:19-21` | "no cookie banner is shown" with no basis; omits the sign-in cookie | PECR regulation 6; E2 | Fixed. States the audit result, names the session cookie, keeps the decision open. |
| F-06 | `src/app/(public)/contact/page.tsx` | No draft banner, indexable, no operator identity | E-Commerce Regulations regulation 6; product rule on draft banners | Fixed. Draft banner, `robots: { index: false }` and a "Who runs Freejoy" placeholder with the missing fields listed. |
| F-07 | `src/app/(public)/about/page.tsx:36-40,58` (before) | Present tense ("puts the terms in plain words next to each one"); does not explain that cashback and vouchers are not free; no mention of affiliate income; "Join the free alerts" implies they are live | CAP 3.23; A1 | Fixed. The page now reads `:47-52` (what Freejoy is), `:53-58` (not everything is free), `:75-80` (affiliate income) and `:81-96` (alerts, which follow `ALERTS_FORM_LIVE`: FREEJ-42). |
| F-08 | `src/app/(public)/terms/page.tsx:24` | "We take care over what we publish" is an unevidenced quality claim; competitions not covered | CAP 3.7; G1 | Fixed. |
| F-09 | `src/components/legal/legal-page.tsx` | Draft banner could only come from `LegalPage`, so the contact page could not reuse it | Product rule | Fixed. `DraftBanner` exported and reused. |

### I.2 Findings in files not owned by this story

The table is the FREEJ-26 review as written. FREEJ-30 then applied N-01 to N-06, N-09, N-11 and N-13, and consciously kept N-10 and N-14; N-07, N-08 and N-12 were finished after FREEJ-24 merged. The outcome of every row is in I.3.

| ID | File:line | Severity | Problem | Suggested wording |
| --- | --- | --- | --- | --- |
| N-01 | `src/components/site/announcement-bar.tsx:9` and `:10` | High | "Good stuff. Zero spend." and "Your daily dose of the good stuff, for £0." Claims nothing costs money, contradicting cashback, vouchers, postage and card trials; "daily" implies a daily refresh the site does not promise. CAP 3.23; Schedule 20 paragraph 23. | Mobile: "Free offers, terms upfront." Desktop: "Free samples, trials and offers, with the terms in plain words." |
| N-02 | `src/config/site.ts:11` (rendered by `site-footer.tsx:11`); `site.ts:9` | High / Low | Tagline "Good things, zero spend." Same issue. The description says "genuine", which implies a verification the preview does not have. | Tagline: "Good things, terms upfront." Description: "Discover UK free samples, trials and offers, with postage and terms shown before you click." |
| N-03 | `src/components/home/home-hero.tsx:16` and `:27` | High / Low | Eyebrow "The good stuff, no spend" (same issue). "Genuinely good freebies" is an unevidenced quality claim. | Eyebrow: "Free samples, trials and offers". Body: "Discover freebies, samples and offers from brands you might like. All in one happy place." |
| N-04 | `src/components/offer/offer-format.ts:24-25` (chips) and `:32-33` (CTA labels) | Medium | "FREE PRODUCT", "Get this free sample" and "Start the free trial" appear with no postage or card information in the CTA block, even for a sample with £2.99 postage or a trial that needs a card. CAP 3.23 allows delivery cost but 3.24 needs the commitment to be clear; the owner rule is stricter. | Chips: "SAMPLE", "TRIAL". CTAs: "Request this sample", "Start the trial", followed by a generated line "Postage £2.99." / "Card required. £X a month after day N unless you cancel." |
| N-05 | `src/lib/catalogue/types.ts:136-142` (labels) and `fixtures.ts:219,373,626` (popular), `:296,550,798` (limited), `:425,474,675` (ends_soon) | Medium | "Popular" is an unevidenced popularity claim (CAP 3.7). "Limited" and "Ends soon" read as urgency (CAP 3.30; Schedule 20 paragraph 7) if they are editorial toggles rather than facts. | Remove `popular`. Compute "Ends soon" from the real `endAt`; allow "Limited" only when the provider states a cap. See C1/C2. |
| N-06 | `src/components/home/trust-strip.tsx:6-7` | Medium | "No hidden surprises" is an absolute promise; "UK freebies only" excludes vouchers and cashback from the description. | "Terms upfront: postage, eligibility and card needs on each listing". "UK offers: samples, trials, cashback, vouchers and competitions". |
| N-07 | `src/components/home/newsletter-band.tsx:24`, `:28`, `:49` | Medium | "No spam" and "Unsubscribe anytime" describe a service that does not exist yet. The form is a GET form, so the visitor's email address lands in the URL, in hosting logs, in browser history and, if Sentry is enabled, in Sentry transaction and breadcrumb URLs; there is no consent box on the band. | Until Phase 3: "Alerts are not live yet. See how we plan to use your email in the privacy notice." In Phase 3: POST server action, consent checkbox, "We will email you to confirm" (see D2, D3). **Status:** the address-in-the-URL part is fixed (FREEJ-24: plain link, no GET form; FREEJ-31: code audit repeated and Sentry scrubber extended to `email`, see D3.6). |
| N-08 | `src/components/alerts/alerts-form.tsx:90`, `:191-194` | Low | "You can change this anytime" promises a preferences page that does not exist. The consent label says "privacy policy" while the page is titled "Privacy notice". | Phase 3: "Yes, send me Freejoy's freebie alerts by email. I can unsubscribe at any time." and link text "privacy notice". |
| N-09 | `src/components/offer/offer-cta.tsx:101` | Medium | "we may earn a commission if you qualify" is vague ("may") and wrong in substance (commission is paid on a click or sale, not "if you qualify"). | See A2: an "Ad" label before the button and "Freejoy is paid a commission by the brand if you take up this offer through this link." |
| N-10 | `src/components/catalogue/offer-card.tsx` (whole card) | Medium (Phase 3) | No "Ad" label on cards. Not a problem now (no affiliate links) but required once offers link through `/go/`. | See A1. |
| N-11 | `src/components/offer/offer-trust-panel.tsx:27` | Medium (Phase 2) | "Last checked <date>" prints a fixture date, shielded today by the demo badge. | See C4: real editor check only. |
| N-12 | `src/app/(public)/alerts/page.tsx:17-20` | Medium | Email address is read from the query string and prefilled; it appears in URLs, hosting logs and, if Sentry is enabled, Sentry transaction and breadcrumb URLs (a known Phase 1 deviation). | Phase 3: POST action; do not accept `?email=`. **Status:** fixed in FREEJ-24 (`/alerts` ignores `?email=`); FREEJ-31 audited the rest of the app and scrubs `email` from Sentry events (see D3.6). |
| N-13 | `src/components/catalogue/catalogue-header.tsx:41` | Low | "Find your next little free thing." covers cashback and vouchers, which are not free. | "Find your next little treat." |
| N-14 | `src/components/site/site-footer.tsx` | Medium (launch) | No operator name, address or company number. | See H1 once the owner supplies details. |

### I.3 FREEJ-30: what was done with each finding (2026-10-30)

"Reworded" means the copy changed on this branch. "Kept" means it was looked at and left on purpose; the reason
is in the row. Every rewording is an engineering suggestion for the qualified reviewer to confirm (J3), not a
legal conclusion, and none of the new text claims compliance, verification or urgency. Unit tests pin the
wording (`src/__tests__/unit/components/public-copy.test.tsx`, `offer-format.test.ts`, `freshness.test.ts`).

| ID | Finding | File | Result | Before, after and reason |
| --- | --- | --- | --- | --- |
| D-01 | N-01 | `components/site/announcement-bar.tsx` | Reworded | "Good stuff. Zero spend." / "Your daily dose of the good stuff, for £0." became "Good stuff. Terms upfront." / "UK samples, trials and offers, with the terms in plain words." No cost claim and no "daily" (the site promises no daily refresh). |
| D-02 | N-02 | `config/site.ts` (footer on every page, metadata, manifest, JSON-LD) | Reworded | Tagline "Good things, zero spend." became "Good things, terms upfront."; "genuine" dropped from the description ("Discover UK free samples, trials and offers, with postage and terms shown before you click."). "Free samples" stays: it is the offer type, with postage stated before the click. |
| D-03 | N-03 | `components/home/home-hero.tsx` | Reworded, one part kept | Eyebrow "The good stuff, no spend" became "Samples, trials and offers"; "genuinely good freebies ... you'll actually love" became "freebies, samples and offers from brands you might like". Kept: the h1 "Small treats. Big free energy." It is a wordplay headline, not a price or quality claim; the reviewer may disagree. |
| D-04 | N-04 | `components/offer/offer-format.ts` (chips) | Reworded | "FREE PRODUCT" and "FREE TRIAL" chips became "SAMPLE" and "TRIAL". **Owner decision.** CAP 3.23 and 3.24 are met on the offer page by the adjacent POSTAGE and CARD REQUIRED chips, so the strict reading is not forced by the rule. It was taken because the owner's own rule is stricter than the Code ("never say completely free for anything with postage, a card or an entry condition") and a chip should not need its neighbour to be true. Revert is two strings if the owner prefers "FREE". |
| D-05 | N-04 | `components/offer/offer-format.ts` (CTA labels) | Reworded | "Get this free sample" became "Request this sample"; "Start the free trial" became "Start the trial". Cashback, voucher and competition labels were already neutral and are unchanged. |
| D-06 | N-04 | `offer-format.ts` `ctaConditions()`, `offer-hero.tsx`, `offer-cta.tsx`, `outbound-link.tsx` | Added | A single line right after the button, generated from the offer's own fields: samples get "Postage: Free." / "Postage: £2.99." / "Postage: Check the brand's terms."; trials that need a card get "A payment card is required to start."; other types get nothing. It sits before the commission sentence on real offers. The line is the accessible description of the button or link (`aria-describedby` through `useId`; `OutboundLink` forwards the prop), so a screen-reader user hears it with the button without the button's name changing (WCAG 2.5.3). This is B2/B3 acceptance criteria applied to the button, not new copy. |
| D-07 | N-04 | `lib/catalogue/types.ts` `OFFER_TYPE_LABELS` ("Free sample", "Free trial"), `components/catalogue/offer-card.tsx` | Kept wording, postage added to cards | **Owner decision (cautious line, owner can revert).** The type names stay "Free sample" and "Free trial" on the card badge and the type filter: they are the offer-type taxonomy, and CAP 3.23 allows "free sample" when the postage is the only cost and is stated. To meet the strict reading, a card for a `free_sample` offer now shows "Postage: Free", "Postage: £2.99" or "Postage: Check terms" on its own line under the title (same helper as the offer page, so it cannot disagree; real and demo offers alike). It sits above the footer row, outside it, so the footer and the two-column layout at 320px do not change width. Cards for trials show no card-requirement line. To revert: delete the `offerType === 'free_sample'` block in `offer-card.tsx` and its tests.
| D-08 | N-05 | `lib/catalogue/freshness.ts` (new), `catalogue/offer-card.tsx` | Reworded (rule added) | Real (API) offers never show "Popular", "Ends soon" or "Limited": an editor can set them with nothing behind them (CAP 3.7, 3.30). A demo fixture still shows them, next to its demo badge. "New today" and "Just added" are kept for real offers: they state recency, not scarcity or popularity. They are still editor-set, not computed, so C1/C2 keep the Phase 3 follow-up (compute "Ends soon" from the real end date; back "Popular" with a recorded metric or delete it). The API and the mapper still carry the value, so turning a label back on later is a change in `displayFreshness()` only. |
| D-09 | N-06 | `components/home/trust-strip.tsx` | Reworded | "UK freebies only / Relevant local offers" became "UK offers / Samples, trials, cashback, vouchers and competitions". "No hidden surprises / What you pay and what you give, stated upfront" became "Clear labels / Cashback and vouchers are labelled for what they are" (the first item already promises terms in plain words, so the absolute is gone rather than repeated). The region name "Our promises" became "How we list offers". Still no digits. |
| D-10 | N-10 | `components/catalogue/offer-card.tsx` | Kept | The "Ad" label is not duplicated on the card. FREEJ-23's `AffiliateCta` (`outbound-link.tsx`) already renders "Ad" before the only `/go/` link, the button on the offer page, and a card links to that offer page, not to `/go/`. A1 criterion 3 asks for the label on the card as well when the offer carries an affiliate link. That is a reviewer judgement (J1), so it is left open and in `Needs professional review`. |
| D-11 | N-09 | `components/offer/offer-cta.tsx` | Reworded | Demo-offer note "on real offers we may earn a commission if you qualify" became "real offers are marked "Ad" and say how Freejoy is paid". "May" and "if you qualify" are gone. The real-offer sentence in `AffiliateCta` was already the A2 wording and is unchanged. |
| D-12 | N-11 | `components/offer/offer-trust-panel.tsx` | Reworded | A demo offer no longer says "Last checked <date>". It says "Sample date <date>", "Sample listing: Freejoy has not checked this offer.", shows an info icon instead of the tick, and names the region "Offer details". A real offer keeps "Last checked <date>" and the tick (the date comes from the editor's `last_verified_at`, and an offer without one is not shown at all). |
| D-13 | N-13 | `components/catalogue/catalogue-header.tsx` | Reworded | "Find your next little free thing." became "Find your next little treat." (N-13's suggestion), since cashback and vouchers are listed too. The "All freebies" breadcrumb and "Discover samples, trials and lovely surprises." are kept. |
| D-14 | N-14 | `components/site/site-footer.tsx` | Kept | No operator name, address or company number is shown, and none is invented. The contact page carries the marked placeholder ("have not been added yet"). Adding a second placeholder to the footer would repeat it on every page. At launch the footer must read the operator fields from `siteConfig` (H1 acceptance criteria) once the owner has supplied them (K1, K2). `siteConfig.supportEmail` still defaults to `support@example.com` and is not shown on any public page; do not render it until K2 is done. |
| D-15 | N-07, N-08, N-12 | `newsletter-band.tsx`, `alerts-form.tsx`, `alerts/page.tsx` | Reworded after FREEJ-24 merged | FREEJ-24 already fixed the structural parts: the band is a plain link to `/alerts` (no GET form, no email in the URL: N-07 and N-12) and `/alerts` ignores any `email` parameter. FREEJ-30 then fixed the copy. Band: "A regular edit of fresh freebies. No spam, just the good stuff." became "Alerts about offers in the categories you choose." (no cadence promise), and "No spam. Unsubscribe anytime." became "Only the alerts you choose. Unsubscribe from any email." Form: "You can change this anytime." (no preferences page exists) became "Unsubscribe from any email to stop them."; the footer line lost "No spam · no sold email lists" and reads "Unsubscribe in one click from any email" (the one-click endpoint is built, `alerts/unsubscribe/one-click`; "no sold email lists" has no backing statement in the privacy notice, so it is dropped until the reviewer adds one). **Kept on purpose:** the consent text in `src/lib/alerts/consent.ts` ("... I can unsubscribe at any time. See the privacy policy.") is stored proof of consent, so it is unchanged and a test pins it; the link text "privacy policy" versus the page title "Privacy notice" (N-08's second point) therefore stays until the reviewer signs the wording off (J5), at which point the stored text version has to change with it.
| D-16 | none | "freebie" wording elsewhere ("Explore freebies", "All freebies", "View freebie", "Get free alerts") | Kept | "Freebie" is the product's vocabulary, not a price claim made about one offer, and every page that lists offers states postage, card and type separately. The reviewer may still prefer fewer "free" words around cashback and vouchers. |

Decisions waiting for the owner or the reviewer:

1. Owner: strict reading of the "free" chips and buttons (D-04, D-05, D-06), and whether the type names "Free
   sample" and "Free trial" on cards and filters follow it (D-07).
2. Owner: whether to keep "Big free energy" (D-03) and the "freebie" vocabulary (D-16).
3. Reviewer: the new slogans, tagline, trust strip and demo-offer disclosure line (J3), and whether a card needs
   its own "Ad" label (D-10, J1).
4. Owner: K1 and K2 are still blocking the footer and contact page placeholders (D-14).
5. Phase 3: replace the editor-set freshness labels with computed or evidenced ones (D-08).

---

## J. Needs professional review

A qualified reviewer (solicitor with data-protection and advertising experience, or equivalent) must decide
these. Engineers must not.

1. Final wording of the "Ad" label and the commission sentence next to each affiliate link (A2), and whether
   non-commission listings need any label.
2. Whether the Sponsored label and the sponsored listings wording are sufficient, and whether network-specific disclosure terms apply (A6, affiliate network contract).
3. The replacement wording for the "zero spend", "for £0" and "no spend" slogans (B7) and the other FREEJ-30 rewordings listed in I.3 (D-01 to D-09, D-11 to D-13).
4. How the DMCC Act subscription-contract rules affect the way trial offers are described (B3).
5. Whether the soft opt-in is unavailable for alerts, as reasoned in D1, and the final consent wording (D2).
6. Whether an Article 21 notice is needed alongside consent, and minimum sign-up age (D7, D8).
7. Lawful bases per purpose, whether click events are personal data, retention periods, processors and any
   transfer outside the UK, including Sentry and the hosting provider (F1, F2, F4).
8. Whether to store any IP address or user agent as consent evidence (D4).
9. Whether the appearance exception or strictly-necessary covers the theme provider reading the `theme` key on every page load when nothing is ever written (E2).
9. The cookie notice, the conclusion that no banner is needed, and any future analytics choice (E3).
10. Whether Freejoy may ever run its own competition or free draw, and the Gambling Act 2005 position (G1).
11. Company-law disclosures required on the site for the chosen legal form (H1) and the ICO fee exemption position (H2).
12. Limitation of liability, governing law, acceptable use and takedown wording in the terms.
13. Sign-off of every legal page. Only then may the draft banner come down; until then "Last reviewed" stays "Not yet reviewed".

## K. Owner actions

These need accounts, registrations or decisions that only the owner can make. No developer should create
them or enter credentials.

| # | Action | Needed by |
| --- | --- | --- |
| K1 | Decide the legal form and operator (company, partnership, sole trader) and the business name. | Before any live offer |
| K2 | Supply the registered name and number (if a company), the registered office or business address, a working contact email address and a VAT number if registered. Set them in site configuration. The contact page placeholder ("Who runs Freejoy") and the footer (no operator line yet, D-14) wait on K1 and K2. | Before launch |
| K3 | Run the ICO data protection fee self-assessment and pay or record the exemption; keep the registration number. | Before processing subscriber data |
| K4 | Choose the email-sending provider, the hosting and error-monitoring set-up; get each provider's data-processing terms; note the transfer mechanism for any provider outside the UK. | Phase 3 |
| K5 | Join the affiliate networks (or direct programmes), read their disclosure terms, and keep the signed terms for the reviewer. | Phase 3 |
| K6 | Verify the cookie and storage audit in a browser against a production build with the real environment variables set (Application tab and network log), including `/login` and `/signup` (Firebase Auth IndexedDB before sign-in), and record the result in `docs/evidence/`. | Before launch |
| K7 | Appoint the qualified legal reviewer, give them the Needs professional review list, and keep their written sign-off. | Before launch |
| K8 | Decide the minimum sign-up age, the retention periods and the "last checked" freshness threshold. | Phase 3 |
| K9 | Decide whether Freejoy will ever run its own giveaways. Until the reviewer answers G1, it does not. | Any time |
| K10 | At launch, remove `robots: { index: false }` from the about, contact, privacy, cookies, terms and affiliate-disclosure pages (after the reviewer signs each off) and flip the unit tests that assert it. | Launch |

## L. Limits of this research

- WebFetch returns a model-written extract. Every rule number above was read from such an extract and should be
  re-read on the official page by the reviewer.
- Not fetched or only seen as a search snippet: the ASA/CMA influencers' guide (S7), the Names and Trading
  Disclosures Regulations 2015 (S20), the Gambling Act 2005, UK GDPR Article 5, the ICO fee exemption page,
  the ICO storage-and-access guidance chapters on consent in practice and on the "strictly necessary" example
  list, and PECR regulation 23. Each is marked "unverified, to confirm" where used.
- The cookie audit is a code review plus a runtime check on a local production build. It has not been run against a deployment with real environment variables (K6).
- Nothing here has been reviewed by a lawyer.
