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
| `--limit N` | 500 (1 to 5000) | Most URLs checked in one run. Offers least recently verified (`last_verified_at`) come first. |
| `--batch-size N` | 200 (1 to 1000) | Offers per committed expiry batch. |
| `--concurrency N` | 5 (1 to 20) | Requests in flight at once. A single host is never asked twice at the same time. |
| `--max-seconds N` | 900 (1 to 86400) | Time limit for the **request phase only**: it does not bound expiry, the read of the URLs to check, or shutdown. Requests still unfinished are cancelled and their targets are reported `inconclusive / not_checked`; the exit code stays 0. |

| Exit | Meaning |
| --- | --- |
| `0` | The run completed. Unreachable URLs do **not** change this: they are a report, not a failure. |
| `1` | The run itself failed: any error before or during expiry, while reading the URLs, or while checking them. stderr names the error **type** (never its text) and how many offers were already expired before the failure (those batches stay committed; run it again to continue), like `prune_click_events`. "Check DATABASE_URL" is only added when nothing had been expired yet. A failure to close the database engine afterwards is ignored and cannot turn a finished run into a failure. |
| `2` | Bad arguments (a value below 1, a value above its cap, an unknown flag). |

stdout is a few count lines, for example:

```
check-offer-health: expired 3 offer(s) whose end date had passed.
check-offer-health: checked 40 URL(s): reachable 35, inconclusive 2, unreachable 2, refused 1.
check-offer-health: problems by reason: blocked_address 1, client_error 1, dns_failure 1. Offer and link ids are in the log.
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

**A URL on a network's tracking host is never requested, whichever field holds it.** After the provider filter,
`collect_targets` drops every source URL and every `direct` link whose validated host is, or is a subdomain of, any host
in `affiliate_hosts.PROVIDER_HOSTS` (`awin1.com`, `linksynergy.com`, `prf.hn` and the rest, plus `www.awin1.com`,
`click.linksynergy.com` and so on). That covers a source URL pasted from a tracking link and a `direct` link on a
merchant whose website was set to a network host. They are counted in `Targets.tracking_host_skipped` and the summary
line ("N URL(s) on a network tracking host were never requested"). The probe itself stays generic. Tests run every
registered host and a `www.` subdomain through both fields, plus an e2e case.

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
| `inconclusive` | `auth_required`, `method_not_allowed`, `rate_limited`, `probe_error`, `host_skipped`, `not_checked`, `tracking_host` | 401 or 403 (usually a bot filter), 405 after a `GET` retry, 429, our own unexpected error, a request skipped by the host circuit breaker, one cut off by `--max-seconds`, or a tracking-host URL handed to `probe_targets` directly |
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
8. **Bounded run.** After 3 consecutive `timeout` or `connection_error` results for one host (`MAX_HOST_FAILURES`), that
   host's remaining targets are not requested and are reported `inconclusive / host_skipped`; a result that proves the host answered
   resets the count, while refusals (`invalid_url`, `blocked_address`) and `probe_error` neither add to it nor clear it. `--max-seconds` (default 900) caps the request phase with `asyncio.timeout`: what finished is
   reported, the rest is `inconclusive / not_checked`, and the exit code is still 0. Both counts appear in the summary.
9. **Neutral User-Agent:** `<Brand>-LinkCheck/1.0 (+<PUBLIC_SITE_URL>)`, the brand taken from `APP_NAME`.

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
  never be attached (`HttpxUrlProbe` must not be used in a process where Sentry is already initialised, for the
  same reason). No `SENTRY_DSN`, no event, no error. If sending fails, stderr names the error type and the run's exit
  code is unchanged.
- **Failed run.** A run that exits 1 sends **one** event too, after the last request has finished: constant message
  `Offer health check run failed` at level `error` (the unreachable summary stays `warning`), context `offer_health` holding only `stage`, `cause` (the exception type name) and
  `expired` (offers already committed). No URL, id or error text. Same no-op when Sentry is off.

## Scheduling

`.github/workflows/scheduled-housekeeping.yml` ("Scheduled housekeeping", FREEJ-50) runs this command every day at
**03:17 UTC**, followed by `commands.prune_click_events` and `commands.purge_subscribers` (one job, three separate
steps, in that order; each runs even when an earlier one failed). It is outside the `CI gate`: it does not run on
pull requests and nothing requires it.

- **Secrets** (repository secrets; setup in [workflow.md](workflow.md#owner-setup-scheduled-housekeeping)):
  `HOUSEKEEPING_DATABASE_URL` is required and becomes `DATABASE_URL` for the three steps; `SENTRY_DSN` is optional (an
  unset secret means no Sentry event, as everywhere else). The workflow introduces no environment variable in the
  apps. The settings object also insists on production values the commands never read (Upstash, Firebase Admin,
  the internal key, the email HMAC key, the subscriber token secret), so the job sets obvious placeholders for them,
  commented in the workflow. If the secret is empty the run fails at a guard step with a clear message.
- **Run it by hand:** Actions tab, "Scheduled housekeeping", "Run workflow". Two optional inputs apply to this command
  only: `skip_reachability` (expire only: no request, no Sentry summary) and `limit` (a whole number, passed as
  `--limit`; blank keeps the default of 500, the command refuses 0 or more than 5000). A manual run also runs the other
  two commands. Runs never overlap (`concurrency: scheduled-housekeeping`, a second one waits) and the job stops after
  30 minutes; the request phase has its own cap (`--max-seconds`, 900).
- **Where the output goes:** the step log of each command (the count lines and the JSON warning lines for each
  problem, as above), and Sentry when `SENTRY_DSN` is set and there is something to report. Nothing is stored on
  GitHub beyond the log, which holds no URLs, addresses or secrets.
- **What a failure looks like:** a red run on the Actions tab and the email GitHub sends to the person who last changed
  the schedule. The failing step is the one that exited non-zero (1: the run failed, with the error type on stderr and,
  for expiry, how many offers were already committed; 2: bad arguments). Unreachable destinations are **not** a
  failure: the run stays green and the report is in the log and in the Sentry event. Re-running the workflow is safe.
  No step is retried automatically.
- **Limits.** GitHub skips scheduled runs on a repository with no activity for 60 days (the schedule is disabled until
  the workflow is re-enabled on the Actions tab), and it may start a scheduled run some minutes late at busy times. The
  database must accept TLS connections from GitHub-hosted runners (the URL carries `?sslmode=require`, which the
  backend honours; see `app/core/db_url.py`). A run cannot be proven from this repository: the first real run is the
  owner's check.

`commands.import_awin_offers` is deliberately not scheduled: it has not been verified against the live Awin API.

## Known limits

- Report only, by design. A human decides what to do with an unreachable offer; nothing is stored, so the same problem
  is reported again on every run until it is fixed.
- A site that answers every request with a 200 error page, or that blocks bots with a 403, is not detected (the latter
  is `inconclusive`, never a problem).
- A redirect that leads to a dead page reads as reachable: redirects are not followed (see above). Network-provider
  links and URLs on tracking hosts are never checked.
- The tracking-host list is the `/go/` allow-list (`PROVIDER_HOSTS`), not an exhaustive registry of affiliate
  networks. A network domain that is not listed, a short link that redirects to a network, or a merchant subdomain
  CNAMEd to a network would still be requested. `probe_targets` re-checks the list itself (`inconclusive /
  tracking_host`), so any caller is covered as far as the list goes.
- The per-host lock and the failure breaker are keyed on the host **name**, so subdomains of one domain (or two names
  on one address) can be probed in parallel, bounded by `--concurrency`.
- The `GET` fallback after a 405 or 501 sends the stored URL including its query string, as `HEAD` does; the response
  body is never read but the origin's access log will contain the query.
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
