#!/usr/bin/env bash

# Isolated restore and packaged-code compatibility checks for threatlens-recovery.sh.
# Public surface: tlr_run_isolated_drill. Every other function is private.
# shellcheck disable=SC2016

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  printf 'ERROR [E228] recovery_drill_lib.sh must be sourced by threatlens-recovery.sh\n' >&2
  exit 2
fi


_tlr_drill_wait_for_database() {
  local timeout_seconds="$1"
  local deadline=$((SECONDS + timeout_seconds))
  local consecutive_ready_checks=0
  while ((SECONDS < deadline)); do
    # The official image briefly serves from its bootstrap postmaster before
    # restarting. Require a stable SQL window rather than accepting that pulse.
    if docker exec "${DRILL_CONTAINER}" psql --no-psqlrc \
      --set=ON_ERROR_STOP=1 --quiet --username postgres \
      --dbname "${DRILL_DATABASE}" --command "SELECT 1" \
      >/dev/null 2>&1; then
      ((consecutive_ready_checks += 1))
      if ((consecutive_ready_checks >= 3)); then
        return 0
      fi
    else
      consecutive_ready_checks=0
    fi
    if [[ "$(docker inspect --format '{{.State.Running}}' "${DRILL_CONTAINER}" 2>/dev/null || true)" \
      != "true" ]]; then
      return 1
    fi
    sleep 1
  done
  return 1
}


_tlr_drill_revision() {
  docker exec "${DRILL_CONTAINER}" psql --no-psqlrc \
    --set=ON_ERROR_STOP=1 --tuples-only --no-align --username postgres \
    --dbname "${DRILL_DATABASE}" --command \
    "SELECT string_agg(version_num, ',' ORDER BY version_num) FROM alembic_version;" \
    | tr -d '\r' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'
}


_tlr_drill_write_smoke_environment() {
  local output="$1"
  local database_host="$2"
  local database_name="$3"
  local database_user="$4"
  local database_password="$5"
  THREATLENS_RECOVERY_DATABASE_USER="${database_user}" \
  THREATLENS_RECOVERY_DATABASE_PASSWORD="${database_password}" \
    compose config --format json | THREATLENS_RECOVERY_DATABASE_USER="${database_user}" \
      THREATLENS_RECOVERY_DATABASE_PASSWORD="${database_password}" \
      python3 "${SAFETY_HELPER}" write-smoke-env \
        --output "${output}" \
        --database-host "${database_host}" \
        --database "${database_name}"
}


_tlr_drill_run_packaged_code_smoke() {
  local application_image="$1"
  local network="$2"
  local database_host="$3"
  local database_name="$4"
  local database_user="$5"
  local database_password="$6"
  local container_name="$7"
  local environment_file="${RESTORE_STAGE_DIRECTORY}/${container_name}.env"

  _tlr_drill_write_smoke_environment \
    "${environment_file}" "${database_host}" "${database_name}" \
    "${database_user}" "${database_password}" \
    || return 1
  DRILL_APP_CONTAINER="${container_name}"
  if ! docker run --name "${DRILL_APP_CONTAINER}" --network "${network}" \
    --env-file "${environment_file}" --entrypoint sh "${application_image}" -ceu '
      alembic upgrade head
      python - <<'"'"'PY'"'"'
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from app.db.session import engine
from app.main import app

required_tables = {"alembic_version", "api_tokens", "audit_logs", "users"}
with engine.connect() as connection:
    tables = set(inspect(connection).get_table_names())
    missing = sorted(required_tables - tables)
    if missing:
        raise RuntimeError(f"packaged schema smoke is missing tables: {missing}")
    revisions = connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
    if not revisions:
        raise RuntimeError("packaged schema smoke found no Alembic revision")

with TestClient(app, raise_server_exceptions=True) as client:
    response = client.get("/v1/health/live")
    if response.status_code != 200 or response.json() != {"ok": True}:
        raise RuntimeError(f"packaged API liveness smoke failed: {response.status_code}")

print("CURRENT_CODE_SMOKE=passed")
PY
    '; then
    return 1
  fi
  if ! docker rm "${DRILL_APP_CONTAINER}" >/dev/null; then
    return 1
  fi
  DRILL_APP_CONTAINER=""
  rm -f -- "${environment_file}"
}


_tlr_drill_run_quarantine_preflight() {
  local hook="$1"
  local compose_files_text output
  printf -v compose_files_text '%s\n' "${COMPOSE_FILES[@]}"
  output="$(THREATLENS_RECOVERY_PHASE=preflight \
  THREATLENS_RECOVERY_COMPOSE_FILES="${compose_files_text%$'\n'}" \
  THREATLENS_RECOVERY_ENV_FILE="${ENV_FILE}" \
  THREATLENS_RECOVERY_PROJECT_NAME="${COMPOSE_PROJECT}" \
  THREATLENS_RECOVERY_MANIFEST="${VERIFIED_MANIFEST}" \
  THREATLENS_RECOVERY_MANIFEST_HELPER="${STAGED_MANIFEST_HELPER}" \
  THREATLENS_RECOVERY_ARCHIVE_SHA256="${VERIFIED_ARCHIVE_SHA256}" \
  THREATLENS_RECOVERY_DATABASE_CONTAINER="${DRILL_CONTAINER}" \
  THREATLENS_RECOVERY_DATABASE_USER=postgres \
  THREATLENS_RECOVERY_DATABASE_NAME="${DRILL_DATABASE}" \
    "${hook}" preflight)" || return 1
  grep -Fxq 'QUARANTINE_PREFLIGHT=passed' <<<"${output}" || return 1
  grep -Fxq 'QUARANTINE_DATABASE_TARGET=isolated_container' <<<"${output}" || return 1
}


_tlr_drill_schema_checks() {
  local expected_source_revision="$1"
  local actual_source_revision table_count invalid_constraint_count smoke_value
  actual_source_revision="$(_tlr_drill_revision)" || return 1
  [[ "${actual_source_revision}" == "${expected_source_revision}" ]] || return 2

  table_count="$(docker exec "${DRILL_CONTAINER}" psql --no-psqlrc \
    --set=ON_ERROR_STOP=1 --tuples-only --no-align --username postgres \
    --dbname "${DRILL_DATABASE}" --command \
    "SELECT count(*) FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p');" \
    | tr -d '[:space:]')" || return 3
  [[ "${table_count}" =~ ^[1-9][0-9]*$ ]] || return 4

  invalid_constraint_count="$(docker exec "${DRILL_CONTAINER}" psql --no-psqlrc \
    --set=ON_ERROR_STOP=1 --tuples-only --no-align --username postgres \
    --dbname "${DRILL_DATABASE}" --command \
    "SELECT count(*) FROM pg_catalog.pg_constraint WHERE NOT convalidated;" \
    | tr -d '[:space:]')" || return 5
  [[ "${invalid_constraint_count}" == "0" ]] || return 6

  smoke_value="$(docker exec "${DRILL_CONTAINER}" psql --no-psqlrc \
    --set=ON_ERROR_STOP=1 --tuples-only --no-align --username postgres \
    --dbname "${DRILL_DATABASE}" --command \
    "SELECT 1 FROM users LIMIT 1;" | tr -d '[:space:]')" || return 7
  [[ -z "${smoke_value}" || "${smoke_value}" == "1" ]] || return 8
  # Consumed by the sourcing recovery dispatcher.
  # shellcheck disable=SC2034
  DRILL_TABLE_COUNT="${table_count}"
}


tlr_run_isolated_drill() {
  local timeout_seconds="$1"
  local database_image="$2"
  local application_image="$3"
  local quarantine_hook="$4"
  local expected_revision="${VERIFIED_ALEMBIC_REVISION}"
  local resource_suffix
  resource_suffix="$(python3 -c 'import secrets; print(secrets.token_hex(5))')" || return 1
  DRILL_NETWORK="threatlens-recovery-net-${resource_suffix}"
  DRILL_VOLUME="threatlens-recovery-db-${resource_suffix}"
  DRILL_CONTAINER="threatlens-recovery-pg-${resource_suffix}"

  log "Creating an isolated internal restore-drill target"
  docker network create --internal --label threatlens.recovery=restore-drill \
    "${DRILL_NETWORK}" >/dev/null || return 11
  docker volume create --label threatlens.recovery=restore-drill \
    "${DRILL_VOLUME}" >/dev/null || return 12
  docker run --detach --name "${DRILL_CONTAINER}" --network "${DRILL_NETWORK}" \
    --mount "type=volume,source=${DRILL_VOLUME},target=/var/lib/postgresql/data" \
    --env POSTGRES_HOST_AUTH_METHOD=trust --env "POSTGRES_DB=${DRILL_DATABASE}" \
    --label threatlens.recovery=restore-drill "${database_image}" >/dev/null || return 13
  _tlr_drill_wait_for_database "${timeout_seconds}" || return 14

  revalidate_staged_restore_inputs || return 15
  log "Restoring the privately staged archive into the isolated target"
  docker exec --interactive "${DRILL_CONTAINER}" pg_restore \
    --username postgres --dbname "${DRILL_DATABASE}" --exit-on-error \
    --single-transaction --no-owner --no-privileges <"${VERIFIED_ARCHIVE}" || return 16

  _tlr_drill_schema_checks "${expected_revision}"
  local schema_status=$?
  ((schema_status == 0)) || return "$((20 + schema_status))"

  log "Running packaged Alembic migrations and API/schema smoke checks without external routing"
  _tlr_drill_run_packaged_code_smoke \
    "${application_image}" "${DRILL_NETWORK}" "${DRILL_CONTAINER}" \
    "${DRILL_DATABASE}" postgres "unused-trust-password" \
    "threatlens-recovery-api-${resource_suffix}" || return 40

  revalidate_staged_restore_inputs || return 41
  log "Running the exact quarantine-hook preflight against the upgraded isolated archive"
  _tlr_drill_run_quarantine_preflight "${quarantine_hook}" || return 42
  # shellcheck disable=SC2034
  DRILL_UPGRADED_REVISION="$(_tlr_drill_revision)" || return 43
}
