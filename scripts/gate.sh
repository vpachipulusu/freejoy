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

# Stage time limits (FREEJ-64). A stage that hangs must end the gate RED with a clear line, not hold it for ever
# (a gate once lost its failure summary to two stalled pytest workers). EVERY stage has a limit in seconds:
# STEP_LIMIT_SECONDS from the caller, else GATE_STAGE_TIMEOUT_SECONDS (default 900). Two mechanisms, because
# coreutils `timeout` under Git Bash only signals MSYS processes: it ends uv.exe or the npm wrapper and leaves the
# Python controller, the xdist workers, vitest and its forks running, still holding database sessions and the
# advisory locks of tests/db_guard.py (the next gate would then refuse to start).
#   Linux, macOS:   `timeout --kill-after=30` (it signals the whole process group of the stage);
#   Git Bash:       a watchdog that polls the stage and at the limit runs `taskkill /F /T` on its Windows pid
#                   (read from /proc/<pid>/winpid), which ends the whole native process tree;
#   neither:        the stage runs without a limit and the log says so at the start.
GATE_VITEST_TIMEOUT_SECONDS="${GATE_VITEST_TIMEOUT_SECONDS:-1800}"
GATE_PYTEST_TIMEOUT_SECONDS="${GATE_PYTEST_TIMEOUT_SECONDS:-2700}"
GATE_STAGE_TIMEOUT_SECONDS="${GATE_STAGE_TIMEOUT_SECONDS:-900}"
GATE_TAIL_LINES="${GATE_TAIL_LINES:-60}"
# Vitest starts one fork per test file, up to cores-1 at once, and each must report started within 60 s; on a
# loaded machine a smaller pool avoids "Failed to start forks worker ... Timeout waiting for worker to respond".
GATE_VITEST_MAX_WORKERS="${GATE_VITEST_MAX_WORKERS:-8}"
for limit_variable in GATE_VITEST_TIMEOUT_SECONDS GATE_PYTEST_TIMEOUT_SECONDS GATE_STAGE_TIMEOUT_SECONDS GATE_TAIL_LINES GATE_VITEST_MAX_WORKERS; do
  case "${!limit_variable}" in
    ''|*[!0-9]*|0) echo "=== REFUSED: $limit_variable must be a whole number greater than 0 (seconds, lines or workers)"; exit 2 ;;
  esac
done

case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*) ON_MSYS=1 ;; *) ON_MSYS=0 ;; esac
LIMIT_MECHANISM="none"
if [ "$ON_MSYS" -eq 1 ]; then
  if command -v taskkill >/dev/null 2>&1 && [ -r /proc/$$/winpid ]; then LIMIT_MECHANISM="watchdog with taskkill /T (Git Bash)"; fi
elif command -v timeout >/dev/null 2>&1 && timeout --version 2>/dev/null | grep -qi coreutils; then
  LIMIT_MECHANISM="coreutils timeout (process group)"
fi

describe_limit() {  # describe_limit <seconds>
  if [ $(($1 % 60)) -eq 0 ]; then echo "$(($1 / 60)) minutes"; else echo "$1 seconds"; fi
}

STAGE_PID=""
STAGE_TIMED_OUT=0

kill_stage_tree() {  # kill_stage_tree <pid>: end the stage and everything it started
  local pid="$1" winpid=""
  [ -n "$pid" ] || return 0
  if [ "$ON_MSYS" -eq 1 ]; then
    winpid="$(cat "/proc/$pid/winpid" 2>/dev/null || true)"
    if [ -n "$winpid" ]; then taskkill //F //T //PID "$winpid" >/dev/null 2>&1 || true; fi
  else
    kill -TERM "$pid" 2>/dev/null || true
  fi
  kill -KILL "$pid" 2>/dev/null || true
}

on_signal() {
  echo "=== interrupted: ending the running stage and its processes" | tee -a "$LOG"
  kill_stage_tree "$STAGE_PID"
  exit 130
}
trap on_signal INT TERM

run_stage() {  # run_stage <limit seconds> <dir> <command...>: output to the log; sets STAGE_TIMED_OUT; returns its status
  local limit="$1" dir="$2"; shift 2
  local began=$SECONDS rc
  STAGE_TIMED_OUT=0
  case "$LIMIT_MECHANISM" in
    coreutils*)
      (cd "$dir" && exec timeout --kill-after=30 "$limit" "$@") >>"$LOG" 2>&1 &
      STAGE_PID=$!
      wait "$STAGE_PID"; rc=$?
      # `timeout` answers 124 (137 after the grace period); a command can exit with those itself, so the
      # answer only counts when the whole limit has really been used.
      if { [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; } && [ $((SECONDS - began)) -ge "$limit" ]; then STAGE_TIMED_OUT=1; rc=124; fi
      ;;
    watchdog*)
      (cd "$dir" && exec "$@") >>"$LOG" 2>&1 &
      STAGE_PID=$!
      while kill -0 "$STAGE_PID" 2>/dev/null; do
        if [ $((SECONDS - began)) -ge "$limit" ]; then
          STAGE_TIMED_OUT=1
          kill_stage_tree "$STAGE_PID"
          break
        fi
        sleep 1
      done
      wait "$STAGE_PID" 2>/dev/null; rc=$?
      if [ "$STAGE_TIMED_OUT" -eq 1 ]; then rc=124; fi
      ;;
    *)
      (cd "$dir" && "$@") >>"$LOG" 2>&1
      rc=$?
      ;;
  esac
  STAGE_PID=""
  return "$rc"
}

step() {  # [STEP_LIMIT_SECONDS=<n>] step <name> <dir> <command...>
  local name="$1" dir="$2"; shift 2
  local limit="${STEP_LIMIT_SECONDS:-$GATE_STAGE_TIMEOUT_SECONDS}" begin rc began=$SECONDS
  echo "=== $name [$(date '+%H:%M:%S')]" | tee -a "$LOG"
  begin=$(wc -c <"$LOG")
  run_stage "$limit" "$dir" "$@"
  rc=$?
  if [ "$STAGE_TIMED_OUT" -eq 1 ]; then
    echo "=== $name TIMED OUT after $(describe_limit "$limit") and was ended with its process tree (limit: $LIMIT_MECHANISM; raise it with GATE_STAGE_TIMEOUT_SECONDS or the stage's own GATE_*_TIMEOUT_SECONDS if the machine is just slow); what it printed so far is in the log" | tee -a "$LOG"
  fi
  echo "=== $name exit=$rc elapsed=$((SECONDS - began))s" | tee -a "$LOG"
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
echo "=== stage limits: $LIMIT_MECHANISM; default $(describe_limit "$GATE_STAGE_TIMEOUT_SECONDS") per stage, frontend unit $(describe_limit "$GATE_VITEST_TIMEOUT_SECONDS") with at most $GATE_VITEST_MAX_WORKERS workers, backend pytest $(describe_limit "$GATE_PYTEST_TIMEOUT_SECONDS")" | tee -a "$LOG"
[ "$LIMIT_MECHANISM" != none ] || echo "=== WARNING: no way to end a stage on this machine (no coreutils timeout, no taskkill): the limits are NOT enforced" | tee -a "$LOG"
if [ -n "${GATE_SELF_TEST_COMMAND:-}" ]; then
  # Only the stage machinery (time limit, process-tree kill, console tail) on a command of the caller's choosing,
  # with the pytest stage's limit: tests/test_gate_timeout.py. No check runs, so this is never a gate result:
  # the words of the result line do not appear and the exit code is 3 (stage passed) or 1 (stage failed).
  echo "=== SELF TEST ONLY: no check runs; this is not a gate result" | tee -a "$LOG"
  STEP_LIMIT_SECONDS="$GATE_PYTEST_TIMEOUT_SECONDS" step "self test" "$ROOT" bash -c "$GATE_SELF_TEST_COMMAND"
  echo "=== GATE SELF TEST: stage $([ $FAILED -eq 0 ] && echo passed || echo failed); NOT A GATE VERDICT (log: $LOG)" | tee -a "$LOG"
  [ $FAILED -eq 0 ] && exit 3
  exit 1
fi
LEAK_REPORT="$(mktemp)"
export FREEJOY_LEAK_REPORT_FILE="$LEAK_REPORT"  # tests/alembic_support.py appends one line per leaked session it ended
report_leaks() {  # sessions a test left open that the pytest stage had to end (printed also when green)
  echo "=== backend pytest: $(wc -l <"$LEAK_REPORT" | tr -d ' ') leaked sessions were ended" | tee -a "$LOG"
}
step "frontend lint" "$FE" npm run lint
step "frontend typecheck" "$FE" npm run typecheck
STEP_LIMIT_SECONDS="$GATE_VITEST_TIMEOUT_SECONDS" step "frontend unit+coverage" "$FE" env VITEST_MAX_WORKERS="$GATE_VITEST_MAX_WORKERS" npm run test:coverage
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
    report_leaks
    step "alembic upgrade head" "$BE" uv run alembic upgrade head
    step "alembic check" "$BE" uv run alembic check
    step "alembic downgrade base" "$BE" uv run alembic downgrade base
    step "alembic upgrade head (again)" "$BE" uv run alembic upgrade head
  else
    report_leaks
    echo "=== alembic cycle SKIPPED: backend pytest failed" | tee -a "$LOG"
  fi
else
  # Really database-less: no inherited database may be picked up by a *_e2e test or Alembic.
  unset TEST_DATABASE_URL DATABASE_URL_DIRECT
  echo "=== GATE_TEST_DATABASE_URL unset: e2e tests skipped, alembic cycle NOT run" | tee -a "$LOG"
  STEP_LIMIT_SECONDS="$GATE_PYTEST_TIMEOUT_SECONDS" step "backend pytest+cov (no e2e)" "$BE" uv run pytest -n auto --dist loadfile --cov --cov-report=term-missing:skip-covered
  report_leaks
fi
rm -f "$LEAK_REPORT"

echo "=== GATE RESULT: $([ $FAILED -eq 0 ] && echo GREEN || echo RED) (log: $LOG)" | tee -a "$LOG"
exit $FAILED
