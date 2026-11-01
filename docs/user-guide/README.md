# Freejoy user guide

**Word version:** the whole guide, with a contents page and every screenshot, is one file:
[Freejoy-User-Guide.docx](Freejoy-User-Guide.docx). It is built from these pages by
[build-docx](build-docx/README.md); these pages stay the source of truth.

A step-by-step guide to what Freejoy does and how to use it, written for people who are not developers.
Every step has a real screenshot of the app. Pages are plain English; where the app shows a word or label,
the guide uses the same word in **bold**.

**Last updated:** 2026-10-30. The screenshots were taken from the app at commit `5a8851d` on 30 October 2026.

## For visitors (the public site)

| Page | What it explains |
| --- | --- |
| [1. Home and finding your way around](01-home-and-navigation.md) | The home page, the menu, the search box, the phone tab bar, what a freebie card tells you |
| [2. Browse, search and filter](02-browse-and-search.md) | The catalogue: search, categories, offer types, the no-postage filter, sorting, pages, "no results" and error messages, the phone filter drawer, category pages |
| [3. Reading an offer](03-offer-details.md) | The offer page: "Last checked", postage and card notes, eligibility, the "Ad" label, the claim button, what happens when you click, ended offers |
| [4. Email alerts](04-email-alerts.md) | Signing up, the confirmation email, confirming, what you can and cannot change, unsubscribing |
| [5. About, contact, legal pages, 404 and cookies](05-information-pages.md) | The information pages (all still **drafts**), the "page not found" page, what Freejoy stores in your browser |
| [6. Keyboard and accessibility tips](06-keyboard-and-accessibility.md) | Using Freejoy without a mouse, with a screen reader, on a phone or with larger text |

## For administrators (the admin area)

| Page | What it explains |
| --- | --- |
| [Admin 1. Signing in and the admin shell](admin-01-signing-in.md) | Who counts as an admin, how to get to `/admin`, the sidebar, the phone tab bar, the Dashboard page |
| [Admin 2. Offers (freebies)](admin-02-offers.md) | The offers list, search and filters, adding an offer, editing, "Mark verified", publishing, unpublishing, expiring, archiving and what each status means |
| [Admin 3. Categories and merchants](admin-03-categories-and-merchants.md) | Adding, editing, enabling and disabling categories; adding, editing, activating and deactivating merchants |
| [Admin 4. Users and two-factor sign-in](admin-04-users-and-2fa.md) | The user list, search, user details, editing a profile, suspending and restoring access, and two-factor sign-in |
| [Admin 5. Subscriptions and analytics](admin-05-subscriptions-and-analytics.md) | The Subscriptions and Analytics pages (the optional Stripe billing module) |
| [Admin 6. Audit logs, SEO and settings](admin-06-audit-logs-seo-settings.md) | The audit log, and the SEO and Settings pages, which are not fully built yet |

## Please read this first

### The data in the screenshots is sample data

The screenshots were taken from a private test copy of Freejoy running on a computer, not from a live website.

- Every brand in the screenshots is labelled **(Sample)**, for example "Good Beans (Sample)". The 24 offers
  are made-up sample offers created by the project's `seed_catalogue` command. They are not real offers.
- The **Last checked** dates you see (such as "30 October 2026") are dates that the sample data was given.
  Nobody at Freejoy checked these offers. On the real site, "Last checked" is only ever the date an editor pressed
  **Mark verified** on that offer.
- The destination of every "claim" button in the sample data is a placeholder on `example.com`.
- Numbers in the admin screenshots (users, subscriptions, revenue, audit entries) come from an almost empty test
  database: two test accounts and no paying customers. They are not visitor or revenue figures.
- The admin offers list also contains two entries titled "Guide walkthrough sample (test entry)". They were made
  while writing this guide to show the publish steps, and were archived straight afterwards.
- Email addresses in the screenshots are test addresses on `example.com` or `example.test`. They are not real people.

### How the screenshots were taken

- Public pages: a production build of the Next.js site served locally, reading the sample catalogue from a local
  FastAPI backend and a local test database. The alerts sign-up used the backend's **fake email provider**, which
  writes each email to a file instead of sending it. Nothing was sent to anyone and no other website was opened.
- Desktop images are 1440 pixels wide; phone images are 390 pixels wide. A phone image is only included where the
  layout differs from the desktop one.
- Admin pages: sign-in to the real site goes through Firebase, which needs a Firebase project. This guide was
  written without one. The admin screenshots were taken through the project's documented, test-only identity
  server (see [admin-offers.md](../developer/admin-offers.md#journey-4-playwright)), which accepts only two
  throwaway session values that the developer sets when starting it, on a private test database and seeds one admin and one ordinary user. It is not part of the
  application, and the app has no sign-in shortcut. Screens that need a real Firebase sign-in, a Stripe account or
  two-factor authentication are marked **Screen pending**, with the reason (needs the Firebase test project, or a Stripe test account; both are
  owner actions); they have no picture. No image in this guide is a mock-up.

### The legal and information pages are drafts

The About, Contact, Privacy, Cookie, Terms and Affiliate disclosure pages all carry the banner
"Draft — placeholder text pending legal review" and say "Last reviewed: Not yet reviewed". Do not treat their
wording as final or as legal advice. See [page 5](05-information-pages.md).

### Things that are not built yet

Said plainly so nobody goes looking for them: the Contact form does not send messages; there are no "saved" or
favourite offers; the alerts sign-up only sends a confirmation email today (no alert emails go out yet); the
admin Dashboard and SEO pages are switched off ("Not available yet"); the admin Settings page shows statistics
only; and creating affiliate links is not in the admin screens.

## Developer documentation

This guide describes behaviour. How the code is built is in [docs/developer](../developer/README.md).
