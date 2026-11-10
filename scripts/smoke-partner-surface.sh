#!/usr/bin/env bash
# Post-deploy smoke for the partner surface (ADR-013): scripts/smoke-partner-surface.sh <base-url>
#
# Needs no secret. It requests paths under and around /partner/v1 on the real domain and fails when the
# backend answers one of them WITHOUT the X-Freejoy-Surface: partner header, which would mean a request
# reached the backend by some other route than the partner sub-app. It passes whether
# PARTNER_API_ENABLED is on or off: the surface answers 404 and sets the header either way.
#
#   must be the partner surface (404 with the header and no cookie or CORS headers):
#       paths that cannot leave the prefix.
#   may be anything but a backend answer without the header:
#       dot-segment and encoded paths (the edge may normalise them to another path), the bare prefix,
#       look-alikes and the internal docs paths. A JSON error envelope without the header is the
#       backend speaking outside the sub-app; the internal schema must never be served here.
#
# Paths are sent as written (--path-as-is) so curl does not tidy them first.
set -uo pipefail

BASE="${1:?usage: smoke-partner-surface.sh <base-url>}"
BASE="${BASE%/}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
FAILED=0

fail() { echo "FAIL $1: $2"; FAILED=1; }

probe() {  # probe <must|may> <path>
  local mode="$1" path="$2" status headers="$WORK/headers" body="$WORK/body"
  status="$(curl --silent --show-error --path-as-is --max-time 15 \
    --retry 3 --retry-delay 3 --retry-all-errors \
    --output "$body" --dump-header "$headers" --write-out '%{http_code}' "$BASE$path")" || {
    fail "$path" "no response"; return
  }
  local has_header=0
  grep -qi '^x-freejoy-surface: partner' "$headers" && has_header=1
  local envelope=0
  head -c 20 "$body" | grep -q '^{"success"' && envelope=1

  if [ "$mode" = must ]; then
    [ "$status" = 404 ] || fail "$path" "expected 404, got $status"
    [ "$has_header" = 1 ] || fail "$path" "the answer has no partner surface header"
  elif [ "$has_header" = 0 ] && [ "$envelope" = 1 ]; then
    fail "$path" "the backend answered ($status) without the partner surface header"
  fi
  if [ "$has_header" = 1 ]; then
    grep -qi '^set-cookie:' "$headers" && fail "$path" "a partner response sets a cookie"
    grep -qi '^access-control-' "$headers" && fail "$path" "a partner response has CORS headers"
  fi
  if [ "$status" = 200 ] && grep -q '"openapi"' "$body"; then
    fail "$path" "an OpenAPI document is served here"
  fi
  echo "ok   $mode $path -> $status header=$has_header"
}

for path in /partner/v1/x /partner/v1/ /partner/v1/docs /partner/v1/redoc /partner/v1/openapi.json /partner/v1//x; do
  probe must "$path"
done
for path in /partner/v1 /partner/v2/x /partnerx/v1/x /partner/v1/../v1/me /partner/v1/%2e%2e/v1/me \
  /partner/v1/%2e%2e/%2e%2e/openapi.json /v1/me /openapi.json /docs /redoc; do
  probe may "$path"
done

[ "$FAILED" = 0 ] && echo "partner surface smoke: PASS" || echo "partner surface smoke: FAIL"
exit "$FAILED"
