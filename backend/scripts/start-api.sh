#!/usr/bin/env bash
set -euo pipefail

cd /app

if [ "${RUN_MIGRATIONS_ON_STARTUP:-true}" = "true" ]; then
  alembic upgrade head
fi

if [ "${SEED_ADMIN_ON_STARTUP:-true}" = "true" ]; then
  python -m app.scripts.seed_admin
fi

exec uvicorn app.main:app --host 0.0.0.0 --port 8000
