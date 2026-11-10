#!/usr/bin/env bash
# Post-deploy smoke for the partner surface (ADR-013): scripts/smoke-partner-surface.sh <https-base-url>
#
# Needs no secret. It requests paths under and around /partner/v1 on the real domain and fails when the
# backend answers one of them WITHOUT the X-Freejoy-Surface: partner header, which would mean a request
# reached the backend by some other route than the partner sub-app. It passes whether
# PARTNER_API_ENABLED is on or off: the surface answers 404 and sets the header either way.
#
#   must: paths that cannot leave the prefix. 404, the surface header, Cache-Control: no-store, private,
#         no Location, no Set-Cookie and no CORS headers.
#   may:  paths an edge might normalise to another path (dot segments, encoded slashes, //), plus the bare
#         prefix, look-alikes and the internal docs paths. Anything is fine except a BACKEND answer without the
#         surface header. The backend marks every response with X-Request-ID (the frontend never sets it:
#         checked in src/proxy.ts and next.config.ts), and its JSON errors start {"success"}.
#
# Only https is spoken (--proto), the base URL is validated, redirects are followed ONCE on the root to find the
# effective origin (validated again), and no probe follows a redirect.
# SMOKE_LOCAL=1 also accepts http://127.0.0.1[:port] or http://localhost[:port]: for testing this script only.
set -uo pipefail

BASE="${1:?usage: smoke-partner-surface.sh <https-base-url>}"
BASE="${BASE%/}"
HTTPS_ORIGIN='^https://[A-Za-z0-9.-]+(:[0-9]+)?$'
LOCAL_ORIGIN='^http://(127\.0\.0\.1|localhost)(:[0-9]+)?$'
PROTO='=https'
[ "${SMOKE_LOCAL:-0}" = 1 ] && PROTO='=http,https'

valid_origin() {
  [[ "$1" =~ $HTTPS_ORIGIN ]] && return 0
  [ "${SMOKE_LOCAL:-0}" = 1 ] && [[ "$1" =~ $LOCAL_ORIGIN ]] && return 0
  return 1
}

valid_origin "$BASE" || { echo "FAIL base URL must be https://host[:port] with no path: refusing"; exit 2; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
CURL=(curl --silent --show-error --proto "$PROTO" --max-time 15 --retry 3 --retry-delay 3 --retry-all-errors)

# The effective origin: follow redirects once, on the root, then keep only scheme, host and port.
effective="$("${CURL[@]}" --location --proto-redir "$PROTO" --output /dev/null --write-out '%{url_effective}' "$BASE/")" \
  || { echo "FAIL no response from $BASE/"; exit 1; }
EFFECTIVE_BASE="$(printf '%s' "$effective" | sed -E 's|^([a-z]+://[^/]+).*|\1|')"
valid_origin "$EFFECTIVE_BASE" || { echo "FAIL the root redirected to an origin that is not acceptable: refusing"; exit 2; }
[ "$EFFECTIVE_BASE" = "$BASE" ] || echo "note: probing $EFFECTIVE_BASE (the root redirected from $BASE)"

FAILED=0

header() {  # header <name> -> the value of that response header, without the line ending
  grep -i "^$1:" "$WORK/headers" | head -1 | sed -E "s/^[^:]*:[[:space:]]*//; s/\r$//"
}

probe() {  # probe <must|may> <path>
  local mode="$1" path="$2" status failed=0
  fail() { echo "FAIL $path: $1"; failed=1; FAILED=1; }
  status="$("${CURL[@]}" --path-as-is --output "$WORK/body" --dump-header "$WORK/headers" \
    --write-out '%{http_code}' "$EFFECTIVE_BASE$path")" || { fail "no response"; return; }

  local surface request_id envelope=0
  surface="$(header x-freejoy-surface)"
  request_id="$(header x-request-id)"
  head -c 20 "$WORK/body" | grep -q '^{"success"' && envelope=1

  if [ "$mode" = must ]; then
    [ "$status" = 404 ] || fail "expected 404, got $status"
    [ "$surface" = partner ] || fail "the answer has no partner surface header"
    [ "$(header cache-control)" = "no-store, private" ] || fail "Cache-Control is not 'no-store, private'"
    [ -z "$(header location)" ] || fail "the answer has a Location header"
  elif [ "$surface" != partner ] && { [ -n "$request_id" ] || [ "$envelope" = 1 ]; }; then
    fail "the backend answered ($status) without the partner surface header"
  fi
  if [ "$surface" = partner ]; then
    [ -z "$(header set-cookie)" ] || fail "a partner response sets a cookie"
    grep -qi '^access-control-' "$WORK/headers" && fail "a partner response has CORS headers"
  fi
  if [ "$status" = 200 ] && grep -q '"openapi"' "$WORK/body"; then
    fail "an OpenAPI document is served here"
  fi
  [ "$failed" = 0 ] && echo "ok   $mode $path -> $status surface=${surface:-none}"
}

for path in /partner/v1/x /partner/v1/ /partner/v1/docs /partner/v1/redoc /partner/v1/openapi.json \
  /partner/v1/health /partner/v1/v1/me /partner/v1/webhooks/stripe; do
  probe must "$path"
done
for path in /partner/v1 /partner/v2/x /partnerx/v1/x /v1/me /openapi.json /docs /redoc \
  /partner/v1//x /partner/v1/./x /partner/v1/../v1/me /partner/v1/%2e%2e/v1/me \
  /partner/v1/%2e%2e/%2e%2e/openapi.json /partner/v1/a%0Ab /partner/v1/..%2f..%2fhealth; do
  probe may "$path"
done

[ "$FAILED" = 0 ] && echo "partner surface smoke: PASS" || echo "partner surface smoke: FAIL"
exit "$FAILED"
