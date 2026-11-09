# Offer health check (`commands.check_offer_health`)

One command, run by hand or on a schedule, that keeps the catalogue honest in two ways:

1. **Expire** every published offer whose `end_at` has passed.
2. **Report** which live offers point at a destination that no longer answers. Report only: it never edits, hides or
   expires an offer because a URL looks dead.

```bash
# fastapi_backend/, with DATABASE_URL set (and SENTRY_DSN if you want the summary event)
uv run python -m commands.check_offer_health                       # both steps
uv run python -m commands.check_offer_health --skip-reachability   # expire only, no network request at all
uv run python -m commands.check_offer_health --limit 100 --concurrency 3
```

Code: `commands/check_offer_health.py` (arguments, output, exit codes), `app/services/offer_expiry.py`,
`app/services/offer_reachability.py`, `app/repositories/offer_health.py` (queries), `app/core/url_probe.py` (the HTTP
seam) and `app/core/address_policy.py` (which addresses may be connected to). A command is a caller like a router:
the rules live in the services, the SQL in the repository.

## Flags and exit codes

| Flag | Default | Meaning |
| --- | --- | --- |
| `--skip-reachability` | off | Only expire. Makes no network request and sends no Sentry event. |
| `--limit N` | 500 | Most URLs checked in one run. Offers least recently verified (`last_verified_at`, never-verified first) come first. |
| `--batch-size N` | 200 | Offers per committed expiry batch. |
| `--concurrency N` | 5 (1 to 20) | Requests in flight at once. A single host is never asked twice at the same time. |

| Exit | Meaning |
| --- | --- |
| `0` | The run completed. Unreachable URLs do **not** change this: they are a report, not a failure. |
| `1` | The run itself failed (database error, or a failure while reading the URLs to check). stderr names the error **type** and how many offers were already expired before the failure (those batches stay committed; run it again to continue), like `prune_click_events`. |
| `2` | Bad arguments (a value below 1, `--concurrency` above 20, an unknown flag). |

stdout is a few count lines, for example:

```
check-offer-health: expired 3 offer(s) whose end date had passed.
check-offer-health: checked 40 URL(s): reachable 35, inconclusive 2, unreachable 2, refused 1.
check-offer-health: problems by reason: client_error 1, dns_failure 1, blocked_address 1. Offer and link ids are in the log.
```

## Step 1: expire ended offers

Every offer with status `PUBLISHED` and `end_at <= now` (an offer ending exactly now is ended, the same strict rule as
the public catalogue's `is_live`) moves to `EXPIRED`. Offers with no `end_at`, a future `end_at`, or any other status
are never touched.

- **No second state machine.** The change is the `expire` action of `app/services/offer_lifecycle.py`
  (`next_status`), which refuses anything that is not `PUBLISHED`. The admin `POST /v1/admin/offers/{id}/expire`
  endpoint needs a signed-in admin, so the command calls the lifecycle table directly rather than
  `AdminOfferService.transition`.
- **Audit.** Each change writes one `audit_logs` row with the same action as the admin endpoint
  (`admin.offers.expire`, resource `offers`, `before` `{"status": "PUBLISHED"}`, `after` `{"status": "EXPIRED"}`,
  `details` with `reason: end_at_passed` and the `end_at`). There is no user, so the row goes through
  `AuditService.record_system_action`, the path `commands.make_admin` already uses: `user_id` is null (the column
  allows it, the admin audit list shows an empty actor) and `metadata.actor` is `commands.check_offer_health`. No
  migration was needed.
- **Batches, locking, idempotency.** Each batch selects its rows `FOR UPDATE SKIP LOCKED`, sets the status, stages the
  audit rows and commits once, so an offer and its audit row stand or fall together. An offer an editor has open for
  saving at that moment is skipped and picked up by the next run, so the command can never overwrite a concurrent edit.
  Expired rows are no longer selected, so a rerun does nothing.
- **What visitors see.** Nothing changes. The public catalogue already treats `PUBLISHED` with a passed `end_at` as
  ended ([backend-catalogue.md](backend-catalogue.md): 410 and no listing), so this is housekeeping that makes the
  admin list and the stored status agree with what the site shows. One consequence for editors: to bring an expired
  offer back they move `end_at` and **publish again** (all publish rules re-run), instead of only editing the date.

## Step 2: reachability (report only)

### What is requested, and what never is

For offers that are `PUBLISHED` and not ended:

- the offer's `source_url`, and
- its **active** affiliate links whose provider is exactly `direct`, and only when the link's host is one `/go/` would
  follow for that merchant (the merchant's own website host, with or without `www.`;
  [freejoy-affiliate.md](freejoy-affiliate.md#provider-host-allowlist)). A `direct` link that points elsewhere is
  skipped and counted, not requested.

**A link of any network provider is never requested**: `awin`, `cj`, `partnerize`, `rakuten`, `tradedoubler`, anything
not `direct`, and `sample` placeholders as well. Why: a tracking link is how the network counts a click. A request from
a health check, even a `HEAD`, would register as a visit to the network, skew the click and conversion figures behind
the commission reports, and look like click fraud to the programme. The repository leaves those links out in SQL
(`provider = 'direct'`) and the service filters again before anything is sent, with a test per provider, so one mistake
does not reach the network. The cost is that a dead network link is not detected here: watch the network's own
reporting for those.

### Outcomes

| Status | Reason codes | When |
| --- | --- | --- |
| `reachable` | `ok`, `redirect` | 2xx, or 3xx (the redirect is not followed) |
| `inconclusive` | `auth_required`, `method_not_allowed`, `rate_limited`, `probe_error` | 401 or 403 (usually a bot filter), 405 after a `GET` retry, 429, or our own unexpected error |
| `unreachable` | `dns_failure`, `connection_error`, `timeout`, `tls_error`, `client_error`, `server_error` | DNS did not resolve, connection or TLS failed, a deadline passed, any other 4xx, any 5xx |
| `refused` | `invalid_url`, `blocked_address` | never requested: the stored URL failed validation, or its name resolves to a non-public address |

Only `unreachable` and `refused` are problems. `refused` is reported too because a stored URL that fails validation, or
a public-looking name that resolves to a private address, is something an editor or an operator must look at.

### SSRF controls (`app/core/url_probe.py`)

The URLs are written by admins and the command runs inside your network, so each request goes through these controls:

1. **Validation again.** Every URL goes through `validate_https_url` immediately before use: https only, port 443, no
   user info, a DNS name (no IP literal, no single-label host), no control characters. Anything else is
   `refused/invalid_url` and is not resolved or requested.
2. **Resolve here, refuse non-public answers.** The name is resolved by the command, and the check is refused unless
   **every** answer is a globally routable unicast address (`is_public_address`). Refused: unspecified, loopback,
   private, link-local (including the `169.254.169.254` metadata address), carrier-grade NAT (`100.64.0.0/10`),
   documentation and benchmarking ranges, multicast and reserved space, for IPv4 and IPv6; an IPv4-mapped IPv6 address
   is judged as the IPv4 address it wraps; NAT64, 6to4, Teredo, the deprecated IPv4-compatible and site-local ranges are
   refused outright. One bad answer among good ones refuses the lot.
3. **The address that was checked is the address connected to.** The request is sent to the validated IP, with the real
   name in the `Host` header and as the TLS server name (httpx's public `sni_hostname` extension). The client never
   resolves the name a second time, so DNS rebinding cannot swap the address between the check and the connection, and
   the certificate is still verified against the real name (a bad certificate is reported as `tls_error`). This needed
   no new dependency and no private httpx API. If the first address cannot be connected to, up to two more are tried;
   a failure after connecting never moves to another address.
4. **Redirects are not followed.** Choice made: a 3xx means "the destination answers", and nothing more is requested.
   That keeps every request on a URL that was validated and resolved by us and removes the redirect-to-internal-host
   class of attack. The price: a redirect that leads to a dead page is reported as reachable.
5. **Environment proxies are ignored** (`trust_env=False`): a proxy would resolve the name itself and make step 3
   meaningless.
6. **No body is read.** `HEAD` first; `GET` only after a 405 or 501, and the response is closed as soon as the status
   line and headers have arrived.
7. **Deadlines.** Connect 5 s, read 10 s, and 20 s for the whole check. `--concurrency` bounds requests in flight and a
   per-host lock keeps the load on one merchant to one request at a time.
8. **Neutral User-Agent:** `<Brand>-LinkCheck/1.0 (+<PUBLIC_SITE_URL>)`, the brand taken from `APP_NAME`.

Verified by hand against the real network (not part of the test suite): a public site answers `reachable`, a name that
resolves to `127.0.0.1` is `refused/blocked_address` with no connection made, an unknown name is `dns_failure` and an
expired certificate is `tls_error`.

## Reporting

- **Never a URL.** No URL, query string, host or header is printed, logged or sent. The HTTP libraries' own request
  logging (which prints URLs) is held at WARNING by the probe, and `CheckTarget.url` is excluded from `repr`.
- **Log.** One WARNING per problem: `offer link problem: offer_id=… link_id=… target=source_url|affiliate_link
  status=… reason=…` (`link_id=-` for a source URL). Run as a script the command writes the JSON log format to stdout.
- **Sentry.** When `SENTRY_DSN` is set and there is at least one `unreachable` or `refused` result, the run sends
  **one** event, `Offer health check found unreachable or refused URLs`, level warning, with a context `offer_health`:
  counts, a reason histogram, and up to 50 problems as offer id, link id, target kind, status and reason code (plus how
  many more were omitted). It goes through `app.core.sentry.report_message`, so `scrub_event` applies as for every other
  event, and Sentry is started only after all requests are done so the HTTP client's breadcrumbs (which hold URLs) can
  never be attached. No `SENTRY_DSN`, no event, no error. A run that fails sends no event: the exit code is the
  signal. If sending fails the run still exits 0 and stderr names the error type.

## Scheduling

**Nothing schedules this command yet.** Run it by hand, or add a schedule. A daily run is plenty: expiry is idempotent
and the checks are rate-limited by design. Whatever runs it needs `DATABASE_URL` (a database you own), outbound HTTPS
and DNS, and optionally `SENTRY_DSN`; no new environment variable is introduced. A scheduled GitHub Actions workflow or
a host cron entry running `uv run python -m commands.check_offer_health` from `fastapi_backend/` would do; neither
exists. Pair it with `commands.prune_click_events` and `commands.purge_subscribers`, which are also unscheduled.

## Known limits

- Report only, by design. A human decides what to do with an unreachable offer; nothing is stored, so the same problem
  is reported again on every run until it is fixed.
- A site that answers every request with a 200 error page, or that blocks bots with a 403, is not detected (the latter
  is `inconclusive`, never a problem).
- Redirects are not followed (see above), and network-provider links are never checked.
- `--limit` bounds the work per run and orders by staleness, but with no stored state a run that is cut short always
  starts from the same end of the list. Raise `--limit` rather than expect rotation.

## Tests

`tests/core/test_address_policy.py`, `tests/core/test_url_probe.py` (every outcome, redirect and fallback behaviour,
private-address and URL refusals, pinned address, no URL in logs; `httpx.MockTransport` and a fake resolver, no
socket), `tests/services/test_offer_expiry.py`, `tests/services/test_offer_reachability.py`,
`tests/repositories/test_offer_health.py`, `tests/commands/test_check_offer_health.py` (flags, output, exit codes,
Sentry summary), and `tests/commands/test_check_offer_health_e2e.py` against a real Postgres (expiry boundaries, audit
rows, batching, row locks, rollback on a failed audit write, the target queries and the command line). The e2e file
runs only with `TEST_DATABASE_URL` set to a local `*_test` database, like the other `*_e2e.py` suites.
