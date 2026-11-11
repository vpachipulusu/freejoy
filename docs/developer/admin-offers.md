# Admin offers UI

The admin screens for the Freejoy catalogue: the freebies list, the offer editor with its publish workflow, and the
categories and merchants pages. They are server-rendered pages and server actions in `nextjs-frontend/` that call
the admin catalogue API ([backend-admin-catalogue.md](backend-admin-catalogue.md)) through wrappers in
`src/lib/api/`. The browser never calls FastAPI, and nothing here decides who may do what: the API checks the
admin's own permission on every call and writes the audit log.

## Routes

| Route | What it is | API permission it needs |
| --- | --- | --- |
| `/admin/offers` | Stats strip, status chips (and the review queue), search, category filter, offers table with a row menu | `offers.read` (row actions: `offers.publish`) |
| `/admin/offers/new` | Offer form; creates a **draft** | `offers.read` to open, `offers.write` to save |
| `/admin/offers/[id]` | Offer form, status actions, provenance of an imported offer or the retailer panel of a partner submission, affiliate links (read only), card preview | `offers.read`; `offers.write` to save and to apply a retailer update; `offers.publish` for the status actions, for dismissing a source change or a retailer update, and for applying one to a live offer |
| `/admin/categories`, `/new`, `/[id]` | Table, add form, edit form, enable and disable | `offers.read`; `categories.write` for changes |
| `/admin/merchants`, `/new`, `/[id]` | Table, add form, edit form, activate and deactivate | `offers.read`; `merchants.write` for changes |

The nav (`src/components/admin/admin-nav-items.ts`) lists Freebies, Categories and Merchants straight after
Dashboard. On phones the tab bar shows the first three items (Dashboard, Freebies, Categories); the rest sit behind
More. An admin without a permission still sees the pages; the API answers 403 and the page shows "You do not have
permission to do that". The permission names are the backend's closed `ADMIN_PERMISSIONS`; the frontend holds no copy
of them.

## The list (`/admin/offers`)

* **URL parameters** (`offers-query.ts`): `q` (at most 100 characters), `status` (`published`, `draft`, `expired`,
  `archived`), `category` (an id), `review` (only `needed` means anything, every other value is ignored), `page`. The
  search and category select are a plain GET form (no script needed), like `/admin/users`; `status` and `review`
  travel in hidden inputs so a search keeps them. The status chips are links; the current one carries
  `aria-current="true"` (only on the current chip; the unit tests pin it). Archived is a fifth chip
  next to the four in the design so an archived offer can be found; the offers there cannot be edited.
* **Review queue chip.** "Needs review" is the last chip (`?review=needed`) and asks the API for `needs_review=true`:
  imported offers that are drafts or whose source changed since the importer last applied it, never an archived
  offer, never an offer made by hand ([the rule](backend-admin-catalogue.md#review-queue), defined once in the
  backend). The chips replace one another: a status chip or All drops `review`, and the review chip drops `status`
  (a hand-edited URL with both still sends both to the API, which combines them). An empty queue says "Nothing
  needs review".
* **Stats strip** (`offers-stats.tsx`): Total, Published, Drafts, Expired, Archived, Needs review. Each status number
  is the `total_count` of a one-row list request to the API and Total is the sum of the four statuses, so the strip
  always adds up. **Needs review** is one more one-row request with `needs_review=true` (five requests in all;
  `fetchOfferStatusCounts`) and is **not** part of the total: it overlaps the statuses (an imported draft is a draft
  and in the queue). Nothing is estimated or cached: if any request fails, the queue's included, the strip shows an
  error (a 429 says so) and no numbers.
* **Table** (`offers-table.tsx`): Offer (title links to the editor, merchant and type underneath, then a small badge:
  "Source changed" when the API sent `source_changed_at`, which wins, else "Imported" when it sent `import_provider`,
  none for an offer made by hand; the badge is a `StatusPill` with text and a decorative icon, never colour alone), Category,
  Status (`StatusPill`: good = Published, neutral = Draft, warn = Expired, info = Archived), Verified
  (`formatAdminDate`, or "Not verified"), Edit and a row menu. Below 640px each row stacks and cells name their
  column. Category and merchant names come from the two pick lists (see below).
* **Row menu** (`offer-row-actions.tsx`): Edit, then the steps the status allows: Mark verified, Publish or
  Unpublish, Expire, Archive. The allowed set (`offer-status.ts`) mirrors the backend status table and is only a hint;
  a stale menu gets a 409 from the API and a toast. **Archive asks for confirmation** because there is no way back
  in the admin.

## Retailer submissions and the pending-update view

An offer a retailer sent through the partner API ([backend-partner-api.md](backend-partner-api.md#offer-submissions-freej-58)) is a draft with
an `offer_imports` row of provider `partner`, so it is in the review queue with no query change. The list badge says **Submitted by
retailer** with the ref (`partner_ref` on the list read), or **Retailer update waiting** when the marker is set. The offer page replaces the
Provenance panel with the retailer panel: merchant, ref, when it was last sent, and a reminder that the commercial basis is a placeholder.

When the retailer sent an update for an offer an editor has already touched (an edited draft, a live, ended or expired offer) the page also
shows the proposal beside the current content: each changed field is a group with two labelled regions, Current and Proposed, stacked below
640px; fields that did not change are only named. Two actions, both bound to the review token the page loaded **and** the hash of the proposal it
showed (409 `offer_changed` if either moved):

- **Apply retailer update** (`POST /v1/admin/offers/{id}/apply-partner-update`, `offers.write`; also `offers.publish` when the offer is live).
  `AdminPartnerUpdateService` writes the proposal through the normal edit path (`_stage_update`: same validation, publish checks and audit row
  as a hand edit; a proposal never carries the commercial basis, so `_guard_basis_change` has nothing to refuse) and the retailer's one `direct`
  link through the normal link edit (host rule included), then clears the proposal and the marker, in one transaction. An offer that has already
  ended keeps its end date unless the proposal gives a new one in the future. The offer stays "edited by a person", so a later retailer update
  waits again. Audit rows: `admin.offers.update`, `admin.offer_links.update`, `admin.offers.partner_update_applied`.
- **Dismiss retailer update** (`POST .../dismiss-partner-update`, `offers.publish`, after a confirmation) clears the proposal and the marker and
  changes nothing else (`admin.offers.partner_update_dismissed`). The generic *dismiss source change* refuses a partner offer (409), so a
  proposal is never half-cleared.

After either, focus moves to a stable "dealt with" paragraph. The partner sees `declined` when an editor archives a submission that was never
published.

## Commercial basis

The form has a required **Commercial basis** choice in its own section, with nothing selected on a new offer and one line
of help under each option: **Editorial** (no payment or benefit), **Affiliate** (a commission through a network) and
**Sponsored** (the merchant pays Freejoy, or gives it a benefit, for the listing or where it appears; visitors see a
"Sponsored" label). The card preview shows the Sponsored label as soon as Sponsored is chosen. The server action
validates with the same Zod schema (`commercialBasis`, "Choose the commercial basis."); API refusals map to curated
messages by code (an unconfirmed basis reads "its commercial basis has not been confirmed (confirm it on the offer page;
for a live offer, unpublish it, change its links, then publish again)"). Changing it is a change like any other: it is
audited and moves the review token, so a stale page cannot publish over it. **A save also sends the token of the version
the form shows**, which the API needs when the basis changes: a form loaded before somebody else changed the basis gets
"This offer changed since you opened it. Review it again." and nothing is written. Changing the basis of a *published*
offer needs `offers.publish` as well as `offers.write` ("Changing the commercial basis of a published offer needs
permission to publish..."). Rules and the migration: [backend-admin-catalogue.md](backend-admin-catalogue.md#commercial-basis).
Public side: a "Sponsored" badge (text, role `note`, named "Sponsored listing") on the card and the detail page, plus a
sentence beside the button; `/affiliate-disclosure` has a "Sponsored listings" section. Tests:
`admin-offer-commercial-basis.test.tsx`, `commercial-basis.test.ts`, `sponsored-label.test.tsx`.

## The publish workflow

1. **Add freebie** creates a `DRAFT` (the API never creates anything else).
2. **Mark verified** sets "last checked" to now (`offers.publish`). Publishing does not do this for you
   (see [backend-admin-catalogue.md](backend-admin-catalogue.md#publish-rules)).
3. **Publish** needs: the category enabled, the merchant active, a source URL or an active affiliate link, no past
   end date, and a verification. When the API says the offer needs its basis confirmed (`needsBasisConfirmation`:
   active links and none on an affiliate network, see [backend-admin-catalogue.md](backend-admin-catalogue.md#commercial-basis)),
   the page shows a checkbox "I confirm this listing's commercial basis is ..." **above** the buttons. Publish waits until
   it is ticked ("Tick the confirmation to publish."; pressing the held button moves focus to the checkbox, and the
   button is described by that status line); the page then sends the saved basis with the request. If the saved basis is
   one this page does not know, Publish stays held with "Choose the commercial basis in the form and save first.". If a rule fails the toast lists the unmet rules in fixed wording
   (`admin-catalogue-errors.ts`), for example "This offer cannot be published yet: it has not been verified."
4. **Unpublish** returns it to draft; **Expire** marks a published offer ended; **Archive** is terminal.

**Verify and publish act on what you loaded.** The offer page arrives with a `review_token` (`reviewToken` in the
detail view) and **Mark verified** and **Publish** send it back. The API compares it under the offer's row lock and
answers 409 `offer_changed` if the offer, its links, its merchant or its category changed since the page was loaded
(the Awin importer, another admin, an expiry). Then nothing is written, the toast says **"This offer changed since you
opened it. Review it again."**, the page reloads (`router.refresh()` after every action, plus `revalidatePath` of the
list and the offer page on that code) and **the form reloads too**: it follows the new data (`values` with
`keepDirtyValues`), so it never shows the old content beside the new token, and a field the editor has typed in keeps
what they typed. A lasting notice (`role="alert"`, beside the buttons) says "This offer changed since you opened it. This
page now shows the latest version: review it again." until the editor's next successful action. The page tracks one
`seen` token, the last version the editor was shown (the token the page first loaded with). A save or a status step
returns `reviewToken` (after it) and `previousReviewToken` (the state it replaced): when `previousReviewToken` is not
`seen`, somebody else changed the offer first and the notice appears, then `seen` becomes `reviewToken`. A reload that
brings a token other than `seen` (a refused step, a link edited elsewhere, a change while no write was in flight) raises it
too. The editor's own save with nothing foreign in between raises nothing. Save is a full-form write, so a foreign
offer-field edit made before the editor's Save is overwritten by it; the notice is how the editor learns it was there.
After a save the form's saved values become its defaults with no field marked as typed in, so a later change to a field the
editor saved earlier shows up in the form instead of the old text being kept. The buttons wait while a save is
reloading into the page (they would hold the old token). The server action validates the token with Zod
(`reviewTokenSchema`: plain characters, at most 128) and, without a usable one, sends nothing, logs a warning (the action
name only, never the token) and shows the same message. Unpublish, Expire and Archive carry no token: taking an offer
off the site is never blocked by staleness. (See [backend-admin-catalogue.md](backend-admin-catalogue.md#the-review-token).)
**The list's row menu has no Publish or Mark verified**: it shows none of an offer's description, steps, links or source
URL, so approving from it would approve unseen content. It offers Edit and **Review** (both open the offer's page), then
Unpublish, Expire and Archive.

**Imported offers and the review queue.** An offer the Awin importer created has a **Provenance** panel
(`offer-provenance-panel.tsx`, read only, shown only when the API sends `provenance`): network name, external id,
"Last imported" (the importer's `applied_at`: the first import or the last refresh) and "Source changed" (a date, or
No), dates through `formatAdminDate`. While the source has moved on and nobody has reviewed it, the panel says so in
words and, when the status allows any step (not archived, not unknown), offers **Dismiss source change**
(`dismiss-source-change-button.tsx`). The button asks for confirmation, then calls `dismissSourceChangeAction`, wired
like Mark verified: it sends the review token the page loaded and the `source_changed_at` the panel showed (the token does not cover the marker, so a newer one the importer noted meanwhile is refused as a stale page), `OfferReviewProvider` is told how the write ended
(`recordWrite(token, previousToken)`, or `recordFailure('offer_changed')` for a stale token, which raises the same
lasting notice and shows "This offer changed since you opened it. Review it again."), a toast announces the outcome and
`router.refresh()` loads the result. It waits like the other status buttons while the form holds unsaved edits or a save
is reloading. Dismissing never edits the offer and never refreshes the public site; when the API finds nothing to
dismiss (somebody else did it first) the admin is told and the pages refresh. See
[backend-awin-import.md](backend-awin-import.md#the-review-queue-and-the-importers-hashes) for what dismissing does to the
importer's hashes.

**Preview.** A draft has no public page, so the editor shows a card preview drawn from the form as it is typed
(`offer-preview-card.tsx`), labelled "Not public". The link to the live page (`/freebies/<slug>`) appears only when the
status is `PUBLISHED`; it opens in a new tab. Status actions act on the last **saved** version, so while the form beside
them holds unsaved edits they wait (`aria-disabled`, with the reason written out): save first.

**Public pages refresh.** Every status action and a save of a published offer call
`revalidateCatalogue(paths)` (`src/lib/catalogue/revalidate.ts`, from FREEJ-21) with `/freebies` and the offer's own
path; every category or merchant change calls it with `/freebies` only. All of them also call `revalidatePath` for the
admin pages. A failure of that refresh is logged and does not turn a
successful write into an error.

## Server actions

`src/app/actions/admin/offer-actions.ts` (create, update, `offerLifecycleAction`, `dismissSourceChangeAction`),
`category-actions.ts`, `merchant-actions.ts`. Each action:

1. runs `asAdmin` (`src/lib/api/admin-catalogue-action.ts`), which calls `requireAdminSession`
   (`src/lib/api/admin-guard.ts`): a verified session cookie and a confirmed admin, exactly as the `/admin` layout
   decides. Anything else returns the curated refusal and **no API call is made**. The proxy cannot do this check;
   every admin page and action does it on the server.
2. validates its input with the same Zod schema the form uses (`src/lib/admin-catalogue/*-schema.ts`, mirrored from
   the API's Pydantic models) and answers with per-field messages (`fieldErrors`) when it fails;
3. checks ids against `^[A-Za-z0-9_-]{1,128}$` before they reach a URL path;
4. calls the wrapper (`admin-offers.ts`, `admin-categories.ts`, `admin-merchants.ts`) and maps a failure to a
   curated message. The result is `{ success: true, data }` or `{ success: false, error, fieldErrors? }`; a taken or
   invalid slug also arrives as a `slug` field error.

On the client, `settleAction` (`src/lib/admin-catalogue/settle-action.ts`) turns a call that throws (network drop,
mid-request deploy) into the same curated failure, so a form or toast always answers.

**Errors never carry backend text.** `callBackend` now also returns the backend's stable `code` (an optional
`code` on the failure result, added for this story); `catalogueErrorMessage` picks the wording from the code
(`slug_taken`, `illegal_transition`, `offer_changed`, `publish_blocked`, `invalid_url`, ...), then from the status. A refused publish
names its unmet rules only from a fixed table. Unknown codes are ignored. Unexpected failures (5xx, unreachable) are
logged with status and code only.

## Pages check the admin too

The `/admin` layout redirects a non-admin, but layout and page render in parallel, so a page cannot rely on that to
keep it from fetching. Every page under `offers`, `categories` and `merchants` starts with `requireAdminPage()`
(`_lib/require-admin-page.ts`), which asks the same gate as the actions and redirects exactly as the layout does:
`/login` (no session), `/verify-email`, `/api/auth/clear-session` (a session the backend rejected), `/dashboard` (not
an admin). An outage throws, so `error.tsx` renders. It costs two extra backend calls per page view; a unit test per
page proves no wrapper is called after a refusal.

## Forms and accessibility

Forms use react-hook-form with `zodResolver`. Every control has a visible label (`Field`,
`_components/form-field.tsx`); hints and errors are tied to the control with `aria-describedby`, the control gets
`aria-invalid` (and `aria-required` when it is required), and field errors are not also live regions. After a failed submit the first invalid field takes focus (a
server `fieldErrors` entry is set on its field first); a failure that belongs to no field fills a named group ("Could not save") that takes
focus (focused, not also `role="alert"`); a successful save focuses a "Changes saved." status message. While a form has unsaved edits it says "You have unsaved changes." and asks the browser to confirm before the tab is
closed (`beforeunload`). Buttons that start work (the submit button, the row menu, the status buttons, the
enable/disable toggles) use `aria-disabled` while busy rather than `disabled`, so they keep focus and stay announced,
and ignore a second press; a polite status says what is happening. After the archive confirmation focus returns to the
control that started it. Tables are named by their heading. Selects are native `<select>` elements, controls
are at least 44px tall, and the layout is checked at 320, 390 and 1440 px (no horizontal scroll). Dates are entered
in UTC (to the second, so an edit never rounds a stored time) and labelled so.

## Pick lists and limits

(Truncation is shown, not hidden: the filter, the table ("Not in the first 100") and the offer pages each say when a
list holds only the first 100 rows, and the editor always keeps the offer's own category and merchant selectable.)


`_lib/pick-lists.ts` loads categories and merchants once per request (`React.cache`) for the offer form and the
list's names and filter. The API caps a page at 100 rows, so the lists are the first 100; the categories and
merchants pages show a "Showing the first N of M" note if a catalogue ever outgrows that. Disabled categories and
inactive merchants are shown with a suffix so a publish refusal is not a surprise.

Not in this UI (use the API): creating or toggling **affiliate links** (the editor lists them read only),
unarchiving, and optimistic concurrency on edits (a save is last-write-wins, as the API documents).

## Tests

* Unit (Vitest): `src/__tests__/unit/lib/admin-catalogue/` (schemas, status table), `unit/api/admin-catalogue-wrappers.test.ts`
  (URLs, bodies, views, counts), `unit/api/admin-guard-errors.test.ts` (the admin gate and message mapping),
  `unit/actions/admin-catalogue-actions.test.ts` (authorisation failures for every action, validation, refresh),
  `unit/app/admin-offers-list.test.tsx`, `admin-offers-actions-ui.test.tsx` (including the changed-since-opened toast and reload),
  `admin-offers-form.test.tsx` (the form follows new data; the buttons wait for a save's reload),
  `admin-offer-review-notice.test.tsx` (the lasting notice, verify then publish, focus), `admin-offer-edit-page-review-token.test.tsx`
  (also: the page hands the provenance to its panel), `admin-offer-provenance.test.tsx` (the panel, when the dismiss
  button exists, the confirmation, the outcomes), `unit/actions/admin-dismiss-source-change-action.test.ts`.
  The list tests (`admin-offers-list.test.tsx`) cover the review chip, the stat card and the badge.
* Backend: `fastapi_backend/tests/test_e2e_identity_server.py` (the journey server's guards).
* Playwright: `src/__tests__/e2e/admin-offers.spec.ts` (Journey 4 and layout checks).

## Journey 4 (Playwright)

Draft, verify, publish, see it on `/freebies/<slug>` and in the search, unpublish, see it gone, archive. It needs a
seeded backend and an admin session; E2E is not part of the `CI gate`, so run it before opening a PR that changes
these screens and record the result.

**With the Firebase test project** (the normal way, see [local-e2e.md](local-e2e.md)): set `TEST_ADMIN_EMAIL` as for
`admin.spec.ts`, point the app at a seeded backend and run

```bash
export E2E_LIVE_CATALOGUE=1 BACKEND_API_URL=http://127.0.0.1:<port> BACKEND_INTERNAL_KEY=<same key>
npm run test:e2e:local -- src/__tests__/e2e/admin-offers.spec.ts
```

**Without Firebase** (a private test database, no sign-in anywhere): start the backend with the test-only identity
server, which serves the real app but accepts exactly two random session values and seeds a matching admin and
non-admin user:

```bash
cd fastapi_backend
export ENVIRONMENT=test DATABASE_URL=postgresql+asyncpg://postgres:<pw>@localhost:<port>/<your_test_db>
export BACKEND_INTERNAL_KEY=<32+ random chars> E2E_ADMIN_SESSION_COOKIE=<24+ random chars> E2E_PLAIN_SESSION_COOKIE=<24+ random chars>
uv run alembic upgrade head && uv run python -m commands.seed_catalogue
uv run python -m tests.e2e_identity_server --port 8171
```

It refuses to start unless `ENVIRONMENT` is `local` or `test`, the database host is loopback, the database name ends
in `_test` and both values are long and different; it binds to 127.0.0.1; it lives under `tests/` and is never part of the application image: `fastapi_backend/.vercelignore` keeps `tests/`,
`.env*` and caches out of the Vercel bundle (follow-up: the Dockerfile should install with `uv sync --no-dev` too). Then
build and start the app on a production build (never `next dev`) with `BACKEND_API_URL`, `BACKEND_INTERNAL_KEY` and
the same two cookie values, and run Playwright with `E2E_LIVE_CATALOGUE=1 E2E_ADMIN_SESSION_COOKIE=...
E2E_PLAIN_SESSION_COOKIE=...`. The spec puts the value in the `session` cookie.
