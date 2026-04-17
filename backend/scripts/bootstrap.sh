#!/usr/bin/env bash
set -euo pipefail

cd /app

retry_step() {
  local label="$1"
  shift

  local attempts="${BOOTSTRAP_RETRY_ATTEMPTS:-5}"
  local delay_seconds="${BOOTSTRAP_RETRY_DELAY_SECONDS:-3}"
  local attempt=1

  until "$@"; do
    if [ "$attempt" -ge "$attempts" ]; then
      echo "bootstrap step failed after ${attempt} attempt(s): ${label}" >&2
      return 1
    fi

    echo "bootstrap step failed (${label}), retrying in ${delay_seconds}s (attempt ${attempt}/${attempts})" >&2
    attempt=$((attempt + 1))
    sleep "$delay_seconds"
  done
}

# Use this one-shot bootstrap entrypoint for schema and admin mutations.
# Steady-state API replicas should keep these disabled.
if [ "${RUN_MIGRATIONS_ON_STARTUP:-false}" = "true" ]; then
  retry_step "alembic upgrade head" alembic upgrade head
fi

if [ "${SEED_ADMIN_ON_STARTUP:-false}" = "true" ]; then
  retry_step "seed admin" python -m app.scripts.seed_admin
fi
