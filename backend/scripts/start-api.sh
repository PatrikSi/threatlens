#!/usr/bin/env bash
set -euo pipefail

cd /app

# Bundled Compose runs migrations in a separate one-shot service. Standalone
# development environments may explicitly retain startup migrations when their
# DATABASE_URL uses a schema owner; runtime credentials cannot perform DDL.
if [ "${RUN_MIGRATIONS_ON_STARTUP:-false}" = "true" ]; then
  alembic upgrade head
fi

if [ "${SEED_ADMIN_ON_STARTUP:-false}" = "true" ]; then
  python -m app.scripts.seed_admin
fi

exec uvicorn app.main:app --host 0.0.0.0 --port 8000
