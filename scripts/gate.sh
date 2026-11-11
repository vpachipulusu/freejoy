#!/usr/bin/env bash
# Complete check suite for merged main, run as ONE job: scripts/gate.sh [<logfile>]
# Covers frontend lint/typecheck/unit/coverage/build + client freshness, backend ruff/format/mypy/
# pytest --cov (incl. *_e2e.py when GATE_TEST_DATABASE_URL is set), OpenAPI schema freshness (internal and partner) and
# the Alembic up / check / down / up cycle. Never run two gates at once: they share the test database.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG="${1:-$ROOT/../gate-$(date +%Y%m%d-%H%M%S).log}"
DB="${GATE_TEST_DATABASE_URL:-}"
FAILED=0

# Stage time limits (FREEJ-64): a stage that hangs must end the gate RED with a clear line, not hold it for ever
# (a gate once lost its failure summary to two stalled pytest workers). The limit is in seconds and comes from the
# caller as STEP_LIMIT_SECONDS; it needs GNU coreutils `timeout` (Git Bash and Linux have it; the Windows system
# `timeout.exe` is not it and is ignored). Without it the stage runs unlimited and the log says so.
GATE_VITEST_TIMEOUT_SECONDS="${GATE_VITEST_TIMEOUT_SECONDS:-1800}"
GATE_PYTEST_TIMEOUT_SECONDS="${GATE_PYTEST_TIMEOUT_SECONDS:-2700}"
GATE_TAIL_LINES="${GATE_TAIL_LINES:-60}"
HAVE_TIMEOUT=0
if command -v timeout >/dev/null 2>&1 && timeout --version 2>/dev/null | grep -qi coreutils; then HAVE_TIMEOUT=1; fi

describe_limit() {  # describe_limit <seconds>
  if [ $(($1 % 60)) -eq 0 ]; then echo "$(($1 / 60)) minutes"; else echo "$1 seconds"; fi
}

step() {  # [STEP_LIMIT_SECONDS=<n>] step <name> <dir> <command...>
  local name="$1" dir="$2"; shift 2
  local limit="${STEP_LIMIT_SECONDS:-0}" begin rc
  echo "=== $name" | tee -a "$LOG"
  begin=$(wc -c <"$LOG")
  if [ "$limit" -gt 0 ] && [ "$HAVE_TIMEOUT" -eq 1 ]; then
    (cd "$dir" && timeout --kill-after=30 "$limit" "$@") >>"$LOG" 2>&1
  else
    [ "$limit" -le 0 ] || echo "=== $name: no GNU timeout on PATH, the limit of $(describe_limit "$limit") is not enforced" | tee -a "$LOG"
    (cd "$dir" && "$@") >>"$LOG" 2>&1
  fi
  rc=$?
  if [ "$limit" -gt 0 ] && [ "$HAVE_TIMEOUT" -eq 1 ] && { [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; }; then
    echo "=== $name TIMED OUT after $(describe_limit "$limit") and was killed (raise the limit with its GATE_*_TIMEOUT_SECONDS variable if the machine is just slow); what it printed so far is in the log" | tee -a "$LOG"
  fi
  echo "=== $name exit=$rc" | tee -a "$LOG"
  if [ "$rc" -ne 0 ]; then
    FAILED=1
    # The log has everything; the console gets the end of this stage, where the failure summary is.
    echo "--- last $GATE_TAIL_LINES lines of $name (full output: $LOG)"
    tail -c +$((begin + 1)) "$LOG" | tail -n "$GATE_TAIL_LINES"
    echo "--- end of $name"
  fi
  return "$rc"
}

# The alembic cycle drops every table of the database it runs on, so GATE_TEST_DATABASE_URL must name a
# local throwaway database: host localhost or 127.0.0.1 and a name ending in _test. Anything that two URL
# parsers could read differently is refused rather than guessed at. Messages are fixed text: no part of the
# URL (it holds a password) is ever printed. GATE_GUARD_ONLY=1 runs just this check (tests/test_gate_database_guard.py).
refuse() {  # refuse <fixed reason>
  local line="=== REFUSED: GATE_TEST_DATABASE_URL: $1; the gate rebuilds this database"
  if [ "${GATE_GUARD_ONLY:-0}" = 1 ]; then echo "$line"; else echo "$line" | tee -a "$LOG"; fi
  exit 2
}

require_local_test_database() {  # require_local_test_database <url>
  local malformed="URL is not in a recognised form"
  case "$1" in *://*) ;; *) refuse "$malformed" ;; esac            # a scheme is required
  # A connection override in the query string would send the connection somewhere else.
  local lower="${1,,}"
  case "$lower" in *\?*host=*|*\?*hostaddr=*|*\?*database=*|*\?*dbname=*|*\?*port=*|*\?*user=*|*\?*password=*) refuse "$malformed" ;; esac
  local url="${1%%\?*}" rest authority host name
  rest="${url#*://}"
  case "$rest" in */*) ;; *) rest="$rest/" ;; esac
  authority="${rest%%/*}"; name="${rest#*/}"                       # the authority ends at the FIRST slash
  case "$authority" in *@*@*) authority="" ;; esac                 # two '@': parsers disagree
  case "$name" in *[@/#]*) name="" ;; esac                         # a plain database name only
  host="${authority##*@}"
  case "$host" in *:*:*) host="" ;; esac                           # IPv6 or a doubled port
  host="${host%%:*}"
  case "$host" in localhost|127.0.0.1) ;; *) refuse "host is not localhost or 127.0.0.1" ;; esac
  case "$name" in ?*_test) ;; *) refuse "database name does not end in _test" ;; esac
}

if [ "${GATE_GUARD_ONLY:-0}" = 1 ]; then
  require_local_test_database "$DB"
  exit 0
fi

FE="$ROOT/nextjs-frontend"; BE="$ROOT/fastapi_backend"
# The gate must give the same answer on every machine: the backend never reads a local
# fastapi_backend/.env (settings, Alembic and the schema commands honour this switch).
export IGNORE_ENV_FILE=1
: >"$LOG"
[ -z "$DB" ] || require_local_test_database "$DB"
if [ -n "${GATE_SELF_TEST_COMMAND:-}" ]; then
  # Only the stage machinery (time limit, console tail, result line) on a command of the caller's choosing, with
  # the pytest stage's limit: tests/test_gate_timeout.py. Nothing else runs.
  STEP_LIMIT_SECONDS="$GATE_PYTEST_TIMEOUT_SECONDS" step "self test" "$ROOT" bash -c "$GATE_SELF_TEST_COMMAND"
  echo "=== GATE RESULT: $([ $FAILED -eq 0 ] && echo GREEN || echo RED) (log: $LOG)" | tee -a "$LOG"
  exit $FAILED
fi
step "frontend lint" "$FE" npm run lint
step "frontend typecheck" "$FE" npm run typecheck
STEP_LIMIT_SECONDS="$GATE_VITEST_TIMEOUT_SECONDS" step "frontend unit+coverage" "$FE" npm run test:coverage
step "frontend build" "$FE" npm run build
step "frontend client fresh" "$FE" npm run check:client

step "backend ruff" "$BE" uv run ruff check .
step "backend format" "$BE" uv run ruff format --check .
step "backend mypy" "$BE" uv run mypy
step "backend openapi fresh" "$BE" uv run python -m commands.check_openapi_schema
step "backend partner openapi fresh" "$BE" uv run python -m commands.check_partner_openapi_schema
if [ -n "$DB" ]; then
  # DATABASE_URL_DIRECT wins over DATABASE_URL in Alembic, so pin it to the test database too.
  export TEST_DATABASE_URL="$DB" DATABASE_URL="$DB" DATABASE_URL_DIRECT="$DB" ENVIRONMENT=test
  if STEP_LIMIT_SECONDS="$GATE_PYTEST_TIMEOUT_SECONDS" step "backend pytest+cov (with e2e)" "$BE" uv run pytest -n auto --dist loadfile --cov --cov-report=term-missing:skip-covered; then
    step "alembic upgrade head" "$BE" uv run alembic upgrade head
    step "alembic check" "$BE" uv run alembic check
    step "alembic downgrade base" "$BE" uv run alembic downgrade base
    step "alembic upgrade head (again)" "$BE" uv run alembic upgrade head
  else
    echo "=== alembic cycle SKIPPED: backend pytest failed" | tee -a "$LOG"
  fi
else
  # Really database-less: no inherited database may be picked up by a *_e2e test or Alembic.
  unset TEST_DATABASE_URL DATABASE_URL_DIRECT
  echo "=== GATE_TEST_DATABASE_URL unset: e2e tests skipped, alembic cycle NOT run" | tee -a "$LOG"
  STEP_LIMIT_SECONDS="$GATE_PYTEST_TIMEOUT_SECONDS" step "backend pytest+cov (no e2e)" "$BE" uv run pytest -n auto --dist loadfile --cov --cov-report=term-missing:skip-covered
fi

echo "=== GATE RESULT: $([ $FAILED -eq 0 ] && echo GREEN || echo RED) (log: $LOG)" | tee -a "$LOG"
exit $FAILED
