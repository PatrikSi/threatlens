#!/usr/bin/env bash
set -euo pipefail

cd /app

# These startup helpers are convenient for the default compose stack and
# single-replica setups. In horizontally scaled production, run migrations and
# admin seeding from one controlled deploy job, then disable them on steady-
# state API replicas.
if [ "${RUN_MIGRATIONS_ON_STARTUP:-false}" = "true" ]; then
  alembic upgrade head
fi

if [ "${SEED_ADMIN_ON_STARTUP:-false}" = "true" ]; then
  python -m app.scripts.seed_admin
fi

exec uvicorn app.main:app --host 0.0.0.0 --port 8000
