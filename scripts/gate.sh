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
}

FE="$ROOT/nextjs-frontend"; BE="$ROOT/fastapi_backend"
# The gate must give the same answer on every machine: the backend never reads a local
# fastapi_backend/.env (settings, Alembic and the schema commands honour this switch).
export IGNORE_ENV_FILE=1
: >"$LOG"
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
  step "backend pytest+cov (with e2e)" "$BE" uv run pytest -n auto --dist loadfile --cov --cov-report=term-missing:skip-covered
  step "alembic upgrade head" "$BE" uv run alembic upgrade head
  step "alembic check" "$BE" uv run alembic check
  step "alembic downgrade base" "$BE" uv run alembic downgrade base
  step "alembic upgrade head (again)" "$BE" uv run alembic upgrade head
else
  echo "=== GATE_TEST_DATABASE_URL unset: e2e tests skipped, alembic cycle NOT run" | tee -a "$LOG"
  step "backend pytest+cov (no e2e)" "$BE" uv run pytest -n auto --dist loadfile --cov --cov-report=term-missing:skip-covered
fi

echo "=== GATE RESULT: $([ $FAILED -eq 0 ] && echo GREEN || echo RED) (log: $LOG)" | tee -a "$LOG"
exit $FAILED
