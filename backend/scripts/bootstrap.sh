#!/usr/bin/env bash
set -euo pipefail

cd /app

# Use this one-shot bootstrap entrypoint for schema and admin mutations.
# Steady-state API replicas should keep these disabled.
if [ "${RUN_MIGRATIONS_ON_STARTUP:-false}" = "true" ]; then
  alembic upgrade head
fi

if [ "${SEED_ADMIN_ON_STARTUP:-false}" = "true" ]; then
  python -m app.scripts.seed_admin
fi
