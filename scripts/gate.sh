#!/usr/bin/env bash
# Complete check suite for merged main, run as ONE job: scripts/gate.sh [<logfile>]
# Covers frontend lint/typecheck/unit/coverage/build + client freshness, backend ruff/format/mypy/
# pytest --cov (incl. *_e2e.py when GATE_TEST_DATABASE_URL is set), OpenAPI schema freshness and
# the Alembic up / check / down / up cycle. Never run two gates at once: they share the test database.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG="${1:-$ROOT/../gate-$(date +%Y%m%d-%H%M%S).log}"
DB="${GATE_TEST_DATABASE_URL:-}"
FAILED=0

step() {  # step <name> <dir> <command...>
  local name="$1" dir="$2"; shift 2
  echo "=== $name" | tee -a "$LOG"
  (cd "$dir" && "$@") >>"$LOG" 2>&1
  local rc=$?
  echo "=== $name exit=$rc" | tee -a "$LOG"
  [ "$rc" -eq 0 ] || FAILED=1
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
step "frontend lint" "$FE" npm run lint
step "frontend typecheck" "$FE" npm run typecheck
step "frontend unit+coverage" "$FE" npm run test:coverage
step "frontend build" "$FE" npm run build
step "frontend client fresh" "$FE" npm run check:client

step "backend ruff" "$BE" uv run ruff check .
step "backend format" "$BE" uv run ruff format --check .
step "backend mypy" "$BE" uv run mypy
step "backend openapi fresh" "$BE" uv run python -m commands.check_openapi_schema
if [ -n "$DB" ]; then
  # DATABASE_URL_DIRECT wins over DATABASE_URL in Alembic, so pin it to the test database too.
  export TEST_DATABASE_URL="$DB" DATABASE_URL="$DB" DATABASE_URL_DIRECT="$DB" ENVIRONMENT=test
  if step "backend pytest+cov (with e2e)" "$BE" uv run pytest -n auto --dist loadfile --cov --cov-report=term-missing:skip-covered; then
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
  step "backend pytest+cov (no e2e)" "$BE" uv run pytest -n auto --dist loadfile --cov --cov-report=term-missing:skip-covered
fi

echo "=== GATE RESULT: $([ $FAILED -eq 0 ] && echo GREEN || echo RED) (log: $LOG)" | tee -a "$LOG"
exit $FAILED
