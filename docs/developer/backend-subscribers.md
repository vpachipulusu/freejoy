# Alerts sign-up: double opt-in, confirm and unsubscribe

**Story:** FREEJ-24. **Decision:** [ADR-012](../adr/0012-email-provider.md) (Brevo, proposed). **Last updated:** 2026-10-30 (FREEJ-32: confirmation sent after the commit).

Visitors sign up for freebie alerts on `/alerts`. Nothing starts until they open an emailed link and press a
button (double opt-in). Consent is recorded in `email_subscribers` (model in
[platform-models.md](platform-models.md)); the email provider only sends mail and holds the list of confirmed
subscribers. Compliance rules and acceptance criteria are in
[`../compliance/uk-affiliate-checklist.md`](../compliance/uk-affiliate-checklist.md) section D.

## Flow

```text
/alerts form --server action--> POST /v1/subscribers          202 once the PENDING row and the token hash are committed
                                                              (the confirmation email goes out AFTER the response)
email link  --> /alerts/confirm?token=...   (page changes nothing; a button press runs the server action)
                 --> POST /v1/subscribers/confirm {token}       CONFIRMED, contact added to the provider list
email link  --> /alerts/unsubscribe?token=... (button)          UNSUBSCRIBED, contact erased at the provider
mail app    --> POST /alerts/unsubscribe/one-click?token=...    same as the button (RFC 8058)
```

The browser never calls FastAPI. The frontend part is one server action file (`src/app/actions/alerts-actions.ts`),
the wrapper `src/lib/api/subscribers.ts`, two pages and one route handler.

## Backend API

All routes need `X-Internal-Key`. Bodies and errors use the usual envelope.

| Route | Answers | Notes |
| --- | --- | --- |
| `POST /v1/subscribers` | 202 `{"success":true,"data":{"status":"accepted"}}` | Same answer for a new, pending, confirmed or unsubscribed address. Limited per client IP (`SUBSCRIBE_IP_POLICY`: 20 per hour, since a shared network can hold several visitors) **and** per keyed hash of the address (`SUBSCRIBE_EMAIL_POLICY`: 5 per hour); both fail closed. 503 when `EMAIL_PROVIDER` is unset and `ENVIRONMENT` is not `local`/`test` (a Vercel preview counts as deployed). A provider outage is **not** a 503 any more: the email is sent after the response, so the failure is logged and withdrawn (see below). |
| `POST /v1/subscribers/confirm` `{token}` | 200 `confirmed`, or 400 `Invalid or expired link` | Forged, expired, used, wrong-purpose and unknown tokens all give the same 400. 503 when the provider list cannot be updated; the link stays usable. |
| `POST /v1/subscribers/unsubscribe` `{token}` | 200 `unsubscribed`, or 400 | Idempotent. The local change is committed first (status, `unsubscribed_at`; `categories` emptied and `provider_subscriber_id` cleared, as [platform-models.md](platform-models.md) requires); a provider failure gives 503 and the next try repeats the provider call. |

Confirm and unsubscribe share `SUBSCRIBER_TOKEN_POLICY` (120 per minute per IP, fail open: an outage must not stop
an unsubscribe).

### Why confirm is a POST (a change from the story's `GET /v1/subscribers/confirm?token=`)

A link in an email is fetched by security scanners and link previews. A state change on `GET` would confirm people
who never clicked, and a token in a query string lands in request logs. So the backend takes the token in a POST
body, and the frontend landing page `/alerts/confirm` does nothing on load: it shows a button, and pressing it runs
the server action. The checklist (D3.9, D3.10) asks for exactly this. If the story must keep a `GET`, add it as a
thin route that redirects to the landing page; do not make it change state.

## Rules the code keeps

- **No enumeration.** `subscribe` returns normally in every state and the frontend shows one neutral message. A
  confirmed subscriber is never changed by a sign-up form submission (anyone can type an address). No path waits
  for the provider before answering (next item), so response time no longer separates a stranger from a known
  address by an HTTP round trip to the provider. A small database difference remains: see Known residual risks.
  The per-IP and per-address limits bound how often it can be probed.
- **Send after the commit (FREEJ-32).** `SubscriberService.subscribe` writes the row, the SHA-256 of the new confirm
  token and `confirmation_sent_at`, builds the email (with that same token), commits, and returns a
  `PendingConfirmation`. The route queues `ConfirmationDelivery.deliver` (`app/services/subscriber_delivery.py`) on
  FastAPI `BackgroundTasks`, which runs after the 202 has been sent. Throttled, confirmed and unsubscribed
  addresses return nothing to send. The background step runs in its own database session (the request session is
  closed by then) and sends the message built before the commit, so the link always matches the stored hash.
  - **`confirmation_sent_at` is a claim made at commit.** It makes the one-hour throttle hold while the mail is in
    flight, so a burst of submissions or a double click cannot send a burst of mail (the row lock serialises them).
  - **When the provider fails** (any exception, including a non-provider one), the claim is withdrawn in a new
    transaction: `confirmation_token_hash` and `confirmation_sent_at` go back to NULL, but only while the stored
    hash is still that mail's hash, so a newer link from a later request is never undone. The row then does not
    claim a mail was sent, no live link exists for an undelivered mail, and the next submission sends at once
    (instead of waiting the hour). The visitor was already told 202 and "check your inbox, then spam", so a
    silent outage costs them one resubmission (the confirmation panel now says so, in state-neutral words:
    "If it has not arrived after a few minutes, you can submit the form again"). The operator sees it: every
    failure is logged as `confirmation email failed: <operation> <status>` (or `unexpected <ExceptionClass>`) and
    reported to Sentry as a `ConfirmationDeliveryFailed` whose only text is that same string. The original
    exception is never attached (its message can hold the address, a token or a response body), and the log call
    is made outside the `except` block because Sentry's logging integration attaches the exception being handled
    to an error log. A failed withdrawal is logged and reported the same way (`withdraw <ExceptionClass>`) and
    swallowed.
  - **A failed brand-new sign-up keeps its row** (PENDING, consent recorded, no token, no claim) until the 30 day
    purge or the visitor's retry, where before the whole row was rolled back. It holds nothing beyond what the
    visitor submitted. The same goes for a PENDING row already past the 30 day retention period: a resubmission
    starts it afresh (`consent_at` = now, new wording), and if that send fails the row keeps the fresh consent
    record and no token. That is deliberate: the visitor did consent just now, the retention clock starts over
    from this request, and the loop is bounded by the one-per-hour resend rule and the rate limits (20 per hour per
    IP, 5 per hour per address). The privacy notice's "a sign-up that is never confirmed is deleted after 30 days"
    already covers a sign-up whose email could never be delivered.
  - **Production without a provider** still answers 503 before anything is stored (`require_email_delivery`).
- **Throttle.** A confirmation is re-sent at most once per hour per address (`confirmation_sent_at`). A newer token
  replaces the stored hash, so older links stop working.
- **Tokens** (`app/core/subscriber_tokens.py`): `base64url(purpose|subject|expiry|nonce).base64url(HMAC-SHA256)`
  under `SUBSCRIBER_TOKEN_SECRET`, verified in constant time. Confirm tokens expire after 24 hours, are single use
  (the row keeps only `sha256(token)`, cleared on use) and are compared in constant time. Unsubscribe tokens carry
  only the subscriber id and never expire, so an old email still works. Nothing about the address is in a token.
- **Privacy.** The address is stored lower-cased (a database CHECK enforces it). No IP address, user agent or
  referrer is stored. Neither the backend nor the frontend logs an address or a token; provider errors keep only
  the operation and HTTP status, never a response body.
- **Consent record.** `consent_text` is the exact wording shown, `consent_at` the time and `consent_source` the
  form (`alerts_page`). The wording lives in one place, `src/lib/alerts/consent.ts`, and the server action sets it,
  so the stored text cannot differ from what the page showed and the browser cannot forge it. The text itself
  is the version; there is no separate version number.
- **Re-subscribing is an operator action.** An UNSUBSCRIBED row is never re-opened by an anonymous submission
  (anyone can type an address, and that would let a stranger restart mail to someone who opted out): the form
  answers the usual 202 and sends nothing, exactly as for a confirmed address. Bringing an unsubscribed address
  back means an operator deleting its row (or waiting for the 12 month purge) so the person can sign up afresh.
  Until then an old unsubscribe link still works (fails safe). The checklist's "re-subscribing revokes earlier
  unsubscribe tokens" (D5.5) is therefore moot.
- **Retention ages from the first request.** Re-sending a confirmation for a PENDING row updates the chosen
  categories and frequency and `confirmation_sent_at`, but not the consent record, so the 30 day clock of an
  unconfirmed sign-up cannot be extended by resubmitting. Once that record is itself older than the retention period (the row is only waiting for the purge), a
  resubmission starts it afresh: new consent time, wording and source, the same as purge then sign up again.

## The email provider

`app/core/email_provider.py` defines the `EmailProvider` protocol (`send`, `upsert_contact`, `delete_contact`) and three implementations, chosen by `EMAIL_PROVIDER`:

| Value | Behaviour |
| --- | --- |
| unset | `DisabledEmailProvider`: nothing is sent; one log line per dropped message, never the address. Local dev and tests. In production the subscribe route answers 503 instead, so nobody is told to check an inbox for mail that will never come. |
| `brevo` | `BrevoEmailProvider` over `httpx` (no new dependency). Needs `EMAIL_PROVIDER_API_KEY`, `EMAIL_PROVIDER_LIST_ID`, `EMAIL_FROM`. **Not yet run against a live account.** |
| `fake` | `FakeEmailProvider`: records calls; if `EMAIL_FAKE_OUTBOX` is set, appends each message as a JSON line to that file. Refused at startup unless `ENVIRONMENT` is `local` or `test` (a Vercel preview with `ENVIRONMENT=production` is refused too). |

The confirmation email's subject and body use `EMAIL_FROM_NAME` (default `Freejoy`) as the brand. That is separate
from `APP_NAME` (the API's title) and from `NEXT_PUBLIC_APP_NAME` (the website's name); keep the three in step.

Settings are validated at startup (`app/core/config.py`): a half-configured Brevo, an unknown provider, a bad
`PUBLIC_SITE_URL`, a short `SUBSCRIBER_TOKEN_SECRET`, or a production start without that secret all stop the
process. Variables are documented in `fastapi_backend/.env.example` and [getting-started.md](getting-started.md).

The confirmation email carries `List-Unsubscribe: <https://site/alerts/unsubscribe/one-click?token=...>` and
`List-Unsubscribe-Post: List-Unsubscribe=One-Click`. A mail provider POSTs to that URL with no browser, which is
why it is a Next.js route handler (`src/app/(public)/alerts/unsubscribe/one-click/route.ts`) and not a server
action; a `GET` there redirects to the unsubscribe page. Sending later alert emails is out of scope: whoever builds
it must keep both headers and the unsubscribe link on every message.

Brevo setup the owner must do (nothing exists yet): an account, a verified sending domain (SPF, DKIM, DMARC), a
list for confirmed subscribers (its numeric id is `EMAIL_PROVIDER_LIST_ID`), and two text contact attributes,
`FREQUENCY` and `CATEGORIES`, which the sync writes.

## Retention

```bash
cd fastapi_backend
uv run python -m commands.purge_subscribers --dry-run   # counts only, changes nothing
uv run python -m commands.purge_subscribers             # deletes
```

It is scheduled: `.github/workflows/scheduled-housekeeping.yml` runs it for real (no `--dry-run`) daily at 03:17 UTC,
as the last of three steps, with the repository secret `HOUSEKEEPING_DATABASE_URL`; run `--dry-run` by hand when you
want counts first. Its output is the count line in the step log; exit 1 (a provider failure, rows kept) turns the run red.
It needs `SELECT` and `DELETE` on `email_subscribers` only. The workflow sets no `EMAIL_PROVIDER` variables, so the
command runs with the disabled provider: that is correct today (the alerts form is not live, no contact exists at a
provider, and an unsubscribed row has its provider id cleared when the contact is erased), but **when Brevo goes
live, add `EMAIL_PROVIDER`, `EMAIL_PROVIDER_API_KEY`, `EMAIL_PROVIDER_LIST_ID` and `EMAIL_FROM` to the purge step**
(as repository secrets), so a row that still carries a provider contact id has it erased before the row is deleted.
Setup: [workflow.md](workflow.md#owner-setup-scheduled-housekeeping). It deletes `PENDING` rows whose consent is older than **30 days** and
`UNSUBSCRIBED` rows whose `unsubscribed_at` is older than **12 months** (the provider contact was already erased
when they unsubscribed) (`PENDING_RETENTION` and
`UNSUBSCRIBED_RETENTION` in `app/services/subscribers.py`). `CONFIRMED` rows are never touched. For a row with a
provider contact the contact is erased at the provider first; if that fails the row is kept and the command exits
1, so no contact is orphaned. Output is counts only. The periods are proposals marked "for legal review" on the
privacy page. Note the unsubscribed address is the only suppression record: once purged, only a fresh opt-in
brings it back.

## Frontend

- **Server action, not an API route.** The form is a client component, and a server action is POST-only and
  refuses a cross-origin `Origin`, so it needs no separate same-origin check, double-submit CSRF token or
  `proxy.ts` allowlist entry (the proxy's `/api/` rules would also need an auth exemption). The rate limit that
  counts is the backend's, keyed on the client IP that `callBackend` forwards (`X-Client-IP`) and on the address.
- **No backend or provider text reaches the browser.** `src/lib/alerts/outcomes.ts` maps a failed call to one of
  four curated messages by HTTP status only. Validation is repeated with Zod in the action.
- **Pages.** `/alerts/confirm` and `/alerts/unsubscribe` are `noindex, nofollow`, set `referrer: no-referrer`, and
  `src/proxy.ts` adds `Referrer-Policy: no-referrer` on those paths. The token is read from the address once,
  kept in memory and removed from the address bar; it is never rendered, so it is not echoed into the page.
- **Home band.** `NewsletterBand` is now a link to `/alerts`. `/alerts` ignores `?email=`. Nothing puts an address
  in a URL any more (finding N-07 of the checklist); the Sentry scrubber also filters `email` (FREEJ-31), for old
  links and for anything typed into a search box.
- **Alerts switch.** `/alerts` shows the form only when `ALERTS_FORM_LIVE=true` is set (server-only, read when
  the server starts; the page is dynamic); otherwise it shows a plain "Alerts aren't open yet" notice, no form, and stays noindex. The
  backend refuses sign-ups in production without a provider as a last line. Set it when a provider is live.
- **Sentry.** The links must carry a token in the query string (a mail app POSTs to the one-click URL directly),
  so `src/lib/sentry/scrub-event.ts` replaces `token=...` with `[Filtered]` in every URL, query string and
  breadcrumb of an event (`beforeSend` and `beforeSendTransaction` in the client, server and edge configs). It
  filters `email` too; see [freejoy-frontend.md](freejoy-frontend.md#error-monitoring-and-addresses-freej-31).
- **Known leaks.** Hosting platform request logs still record the full URL of the landing page load, token
  included, for as long as the platform keeps them (retention is the platform's setting: check it and shorten it
  before launch). A confirm token stops working once used or after 24 hours; an unsubscribe token can only
  unsubscribe that one address.

## Known residual risks (pre-launch follow-ups)

- **Background send is best effort, not a queue.** The email is sent by a FastAPI background task in the same
  process. Starlette runs it inside the ASGI call, after the last response body chunk, so a function freeze right
  after the response is unlikely, but nothing here makes the send durable. If the process is stopped between the
  commit and the send (a crash, a deploy), nothing withdraws the claim: the row says a mail was sent for up to one
  hour (`RESEND_INTERVAL`), then a resubmission sends again. A durable outbox (a table drained by a worker or cron)
  would remove this window and also retry failed sends without a visitor; it is a separate story.
- **PRE-LAUNCH VERIFICATION GATE: the response must really leave before the send.** The timing fix only works if the
  host streams the response before the ASGI call returns. Nothing in the code or the tests can show that for Vercel's
  Python runtime. Before `ALERTS_FORM_LIVE=true` goes to production, verify on a Vercel preview deployment (do not add
  an inline-on-Vercel branch):
  1. **The mail arrives after the 202.** `EMAIL_PROVIDER=fake` with an outbox file is local-only (refused outside
     `local`/`test`), so use a real or test provider, or watch for the absence of a
     `confirmation email failed` log line and check the test inbox.
  2. **The response returns before the provider call.** Measure, from the Next.js side, the duration of
     `POST /v1/subscribers` for an already-confirmed address and for a new one, several times each. The two should be
     within a few milliseconds of each other (see the next item); a gap of a provider round trip (hundreds of ms)
     means the response is buffered until the background task finishes.
  3. **Keep-alive.** Two sequential requests on one connection must not block on the first one's send (the second
     request's duration must not include the first one's provider call).
  4. **If the response is buffered until the ASGI call returns, or background tasks are killed**, the timing gain is
     zero or mails are lost. The fallback is a durable outbox drained by a cron job (a separate story), not an inline
     send.
- **Remaining timing differences on `POST /v1/subscribers`.** No path awaits the provider, so the hundreds of
  milliseconds of an HTTP round trip are gone (once item 2 of the gate above is confirmed), but the database work
  still differs by path. Statements per request, pinned by
  `test_the_statements_each_address_state_runs_match_the_documented_table` (`tests/api/test_subscribers_e2e.py`):

  | Address state | Statements | Ends with |
  | --- | --- | --- |
  | new | `INSERT`, `UPDATE` | `COMMIT` |
  | pending, resend allowed (over an hour since the last mail) | `INSERT` (conflicts), locking `SELECT`, one `UPDATE` | `COMMIT` |
  | pending, mailed within the hour | `INSERT` (conflicts), locking `SELECT` | `ROLLBACK` |
  | confirmed | `INSERT` (conflicts), locking `SELECT` | `ROLLBACK` |
  | unsubscribed | `INSERT` (conflicts), locking `SELECT` | `ROLLBACK` |

  The resend path writes the new choices and the new token in one `UPDATE`. What remains is one statement and a
  write-ahead-log flush on a nearby database, in the order of milliseconds, within the noise of a network round trip
  from the Next.js server. It is not constant time and this change does not claim it is. Rate limiting (20 per hour
  per IP, 5 per hour per address) bounds how many samples a prober gets. If it ever matters, equalise by doing a dummy
  write and commit on the no-op paths or pad every answer to a fixed minimum duration.
- **Client IP off Vercel.** `callBackend` forwards `X-Client-IP` only when `VERCEL=1` (it trusts
  `x-vercel-forwarded-for`, never a raw `X-Forwarded-For`). Anywhere else (local runs, another host) every visitor
  reaches the backend as one address and shares one per-IP bucket, so the 20 per hour IP limit is shared by all.
  Fine for development; a non-Vercel production would need its own trusted forwarding header.

## Local run and tests

```bash
cd fastapi_backend
# send nothing, but let a browser test read the links:
export EMAIL_PROVIDER=fake EMAIL_FAKE_OUTBOX=/tmp/freejoy-outbox.jsonl SUBSCRIBER_TOKEN_SECRET=<32+ chars>
export BACKEND_INTERNAL_KEY=<32+ chars> RATE_LIMIT_EMAIL_KEY=<32+ chars>   # the per-address limit hashes with the latter
uv run uvicorn app.main:app --port 8000
```

- Backend unit tests use `FakeEmailProvider` and the in-memory store in `tests/fakes_subscribers.py`.
  `tests/api/test_subscribers_e2e.py` runs the whole lifecycle on Postgres (`TEST_DATABASE_URL`). Tests prove "the
  provider is not awaited before the response" with a `GatedEmailProvider` (an `asyncio.Event` the test releases)
  and a raw ASGI call that returns as soon as the response is complete; `httpx.ASGITransport` waits for background
  tasks, so ordinary client calls see the email in the fake outbox once they return.
- Frontend: Vitest for the action, form states, pages, route handler and proxy header.
  `src/__tests__/e2e/alerts-lifecycle.spec.ts` (FREEJ-27) runs the journey in a browser when `E2E_EMAIL_OUTBOX` points at the
  same outbox file the backend writes (it skips otherwise); it polls the outbox because the email is written after the response,
  and it also checks that a repeat submission gets the same answer, sends nothing and never reopens an unsubscribed address.
  See [local-e2e.md](local-e2e.md).

## Owner follow-ups

Create the Brevo account and set the variables; verify the sending domain; set a postal address and legal
operator name for the email footer and the consent label; have the consent wording, the 30-day and 12-month
retention periods, the named processor and the minimum age reviewed; then start the frontend with `ALERTS_FORM_LIVE=true`.
