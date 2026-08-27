#!/usr/bin/env bash

# Container-side shell fragments intentionally defer variable expansion.
# shellcheck disable=SC2016

set -Eeuo pipefail
IFS=$'\n\t'
umask 077

readonly EXIT_USAGE=2
readonly EXIT_PREREQUISITE=3
readonly EXIT_VALIDATION=4
readonly EXIT_DATABASE=5
readonly EXIT_DRILL=6
readonly EXIT_REFUSED=7
readonly EXIT_RESTORE=8
readonly RESTORE_CONFIRMATION="RESTORE THREATLENS POSTGRESQL"
readonly ARCHIVE_FILENAME="database.dump"
readonly MANIFEST_FILENAME="manifest.json"
readonly DRILL_DATABASE="threatlens_restore_drill"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPOSITORY_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd -P)"
MANIFEST_HELPER="${SCRIPT_DIR}/recovery_manifest.py"

declare -a COMPOSE_FILES=()
declare -a COMPOSE_COMMAND=()
ENV_FILE="${THREATLENS_ENV_FILE:-${REPOSITORY_ROOT}/.env}"
COMPOSE_PROJECT="${THREATLENS_COMPOSE_PROJECT:-}"
APP_VERSION_OVERRIDE="${THREATLENS_APP_VERSION:-}"

TEMP_BACKUP_DIRECTORY=""
DRILL_CONTAINER=""
DRILL_VOLUME=""
DRILL_NETWORK=""
BACKUP_LOCK_FD=""
COMPLETED_BACKUP_DIRECTORY=""
VERIFIED_MANIFEST=""
VERIFIED_ARCHIVE=""
VERIFIED_APP_VERSION=""
VERIFIED_ALEMBIC_REVISION=""
VERIFIED_ARCHIVE_SHA256=""
VERIFIED_ARCHIVE_SIZE=""
OPERATION_TYPE=""
OPERATION_STARTED_AT=""
OPERATION_METADATA_JSON='{"tool_version":"1"}'
OPERATION_LEDGER_ALLOWED=false
OPERATION_FINISHED=false
CONTROLLED_EXIT=false
RESTORE_REPLACEMENT_ACTIVE=false
RESTORE_ROLLBACK_DATABASE=""


timestamp() {
  date -u +'%Y-%m-%dT%H:%M:%SZ'
}


log() {
  printf '%s INFO %s\n' "$(timestamp)" "$*" >&2
}


warn() {
  printf '%s WARN %s\n' "$(timestamp)" "$*" >&2
}


die() {
  local exit_code="$1"
  local error_code="$2"
  shift 2
  CONTROLLED_EXIT=true
  printf '%s ERROR [%s] %s\n' "$(timestamp)" "${error_code}" "$*" >&2
  finish_operation_best_effort failed "${error_code}"
  exit "${exit_code}"
}


usage() {
  cat <<'EOF'
ThreatLens host-side PostgreSQL backup and recovery utility.

Usage:
  threatlens-recovery.sh [global options] backup [options]
  threatlens-recovery.sh [global options] verify --backup PATH
  threatlens-recovery.sh [global options] drill --backup PATH [options]
  threatlens-recovery.sh [global options] restore --backup PATH [safety options]

Global options:
  --compose-file PATH    Compose file; repeat for overrides (default: docker-compose.yml)
  --env-file PATH        Compose environment file (default: repository .env)
  --project-name NAME    Explicit Compose project name
  --app-version VERSION  Override the repository VERSION value in backup metadata
  --help                 Show this help

Backup options:
  --output-dir PATH      Completed backup directory parent (default: ./backups)

Verify options:
  --backup PATH          Completed backup directory or manifest.json
  --expected-app-version VERSION
                         Refuse a backup from another application version

Drill options:
  --backup PATH          Completed backup directory or manifest.json
  --timeout-seconds N    PostgreSQL startup timeout, 10-600 (default: 90)

Restore options:
  --backup PATH          Completed backup directory or manifest.json
  --confirm TEXT         Must exactly equal: RESTORE THREATLENS POSTGRESQL
  --acknowledge-data-loss
                         Independent opt-in acknowledging destructive replacement
  --quarantine-hook PATH Executable application hook supporting preflight/apply/verify
  --safety-backup-dir PATH
                         Fresh pre-restore backup parent (default: ./backups/pre-restore)
  --allow-app-version-mismatch
                         Explicitly allow restore from another application version

Environment equivalents:
  THREATLENS_COMPOSE_FILE (colon-separated), THREATLENS_ENV_FILE,
  THREATLENS_COMPOSE_PROJECT, THREATLENS_APP_VERSION,
  THREATLENS_BACKUP_DIR, THREATLENS_POST_RESTORE_HOOK.

Redis is deliberately excluded from every command. It is cleared after a successful
destructive PostgreSQL restore and is never backed up or restored.
EOF
}


cleanup_partial_backup() {
  if [[ -z "${TEMP_BACKUP_DIRECTORY}" ]]; then
    return 0
  fi
  local basename
  basename="$(basename -- "${TEMP_BACKUP_DIRECTORY}")"
  if [[ "${basename}" == .threatlens-backup.partial.* ]]; then
    rm -rf -- "${TEMP_BACKUP_DIRECTORY}"
  else
    warn "Refusing automatic cleanup of unexpected temporary path: ${TEMP_BACKUP_DIRECTORY}"
  fi
  TEMP_BACKUP_DIRECTORY=""
}


cleanup_drill_resources() {
  local cleanup_failed=0
  if [[ -n "${DRILL_CONTAINER}" ]]; then
    if ! docker rm --force "${DRILL_CONTAINER}" >/dev/null 2>&1; then
      cleanup_failed=1
    fi
    DRILL_CONTAINER=""
  fi
  if [[ -n "${DRILL_VOLUME}" ]]; then
    if ! docker volume rm --force "${DRILL_VOLUME}" >/dev/null 2>&1; then
      cleanup_failed=1
    fi
    DRILL_VOLUME=""
  fi
  if [[ -n "${DRILL_NETWORK}" ]]; then
    if ! docker network rm "${DRILL_NETWORK}" >/dev/null 2>&1; then
      cleanup_failed=1
    fi
    DRILL_NETWORK=""
  fi
  return "${cleanup_failed}"
}


on_exit() {
  local original_exit="$1"
  set +e
  if ((original_exit != 0)) && [[ "${CONTROLLED_EXIT}" != true ]]; then
    _tlr_restore_emergency_rollback || true
  fi
  if ((original_exit != 0)) && [[ "${OPERATION_FINISHED}" != true ]]; then
    finish_operation_best_effort failed E_UNEXPECTED
  fi
  cleanup_partial_backup
  cleanup_drill_resources
  if [[ -n "${BACKUP_LOCK_FD}" ]]; then
    exec {BACKUP_LOCK_FD}>&- 2>/dev/null
    BACKUP_LOCK_FD=""
  fi
  return "${original_exit}"
}


trap 'on_exit "$?"' EXIT
trap 'exit 130' INT TERM HUP


require_command() {
  local command_name="$1"
  command -v "${command_name}" >/dev/null 2>&1 \
    || die "${EXIT_PREREQUISITE}" "E301" "Required command is unavailable: ${command_name}"
}


validate_project_name() {
  if [[ -n "${COMPOSE_PROJECT}" && ! "${COMPOSE_PROJECT}" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]]; then
    die "${EXIT_USAGE}" "E201" "Compose project name contains unsupported characters"
  fi
}


build_compose_command() {
  COMPOSE_COMMAND=(docker compose --env-file "${ENV_FILE}")
  local compose_file
  for compose_file in "${COMPOSE_FILES[@]}"; do
    COMPOSE_COMMAND+=(-f "${compose_file}")
  done
  if [[ -n "${COMPOSE_PROJECT}" ]]; then
    COMPOSE_COMMAND+=(--project-name "${COMPOSE_PROJECT}")
  fi
}


compose() {
  "${COMPOSE_COMMAND[@]}" "$@"
}


start_operation() {
  local command="$1"
  case "${command}" in
    backup|verify|restore) OPERATION_TYPE="${command}" ;;
    drill) OPERATION_TYPE="restore_drill" ;;
    *) return 0 ;;
  esac
  OPERATION_STARTED_AT="$(timestamp)"
  OPERATION_METADATA_JSON="$(
    python3 "${MANIFEST_HELPER}" ledger-metadata --field tool_version=1
  )" || OPERATION_METADATA_JSON='{"tool_version":"1"}'
  OPERATION_LEDGER_ALLOWED=true
  if [[ "${OPERATION_TYPE}" == "restore" ]]; then
    OPERATION_LEDGER_ALLOWED=false
  fi
  OPERATION_FINISHED=false
}


set_operation_metadata() {
  local metadata
  if metadata="$(python3 "${MANIFEST_HELPER}" ledger-metadata "$@")"; then
    OPERATION_METADATA_JSON="${metadata}"
  else
    warn "Operation metadata was invalid and was replaced with the minimal safe record"
    OPERATION_METADATA_JSON='{"tool_version":"1"}'
  fi
}


finish_operation_best_effort() {
  local status="$1"
  local error_code="${2:-}"
  if [[ -z "${OPERATION_TYPE}" || "${OPERATION_FINISHED}" == true ]]; then
    return 0
  fi
  OPERATION_FINISHED=true
  if [[ "${OPERATION_LEDGER_ALLOWED}" != true || ${#COMPOSE_COMMAND[@]} -eq 0 ]]; then
    return 0
  fi

  local run_id error_message=""
  run_id="$(python3 -c 'import uuid; print(uuid.uuid4())' 2>/dev/null)" || {
    warn "System operation history could not be recorded; UUID generation failed"
    return 0
  }
  if [[ "${status}" == "failed" ]]; then
    error_message="Host recovery command failed; inspect the host log using the recorded error code."
  fi

  if ! compose exec -T \
    --env "THREATLENS_OPERATION_ID=${run_id}" \
    --env "THREATLENS_OPERATION_TYPE=${OPERATION_TYPE}" \
    --env "THREATLENS_OPERATION_STATUS=${status}" \
    --env "THREATLENS_OPERATION_STARTED_AT=${OPERATION_STARTED_AT}" \
    --env "THREATLENS_OPERATION_METADATA=${OPERATION_METADATA_JSON}" \
    --env "THREATLENS_OPERATION_ERROR_CODE=${error_code}" \
    --env "THREATLENS_OPERATION_ERROR_MESSAGE=${error_message}" \
    db sh -ceu '
      export PGCONNECT_TIMEOUT=3
      export PGOPTIONS="-c statement_timeout=3000 -c lock_timeout=1000"
      table_exists="$(psql --no-psqlrc --set=ON_ERROR_STOP=1 --tuples-only --no-align \
        --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --command \
        "SELECT to_regclass('"'"'public.system_operation_runs'"'"') IS NOT NULL;" | tr -d "[:space:]")"
      [ "$table_exists" = "t" ] || exit 42
      exec psql --no-psqlrc --set=ON_ERROR_STOP=1 --username "$POSTGRES_USER" \
        --dbname "$POSTGRES_DB" \
        --set=operation_id="$THREATLENS_OPERATION_ID" \
        --set=operation_type="$THREATLENS_OPERATION_TYPE" \
        --set=operation_status="$THREATLENS_OPERATION_STATUS" \
        --set=operation_started_at="$THREATLENS_OPERATION_STARTED_AT" \
        --set=operation_metadata="$THREATLENS_OPERATION_METADATA" \
        --set=operation_error_code="$THREATLENS_OPERATION_ERROR_CODE" \
        --set=operation_error_message="$THREATLENS_OPERATION_ERROR_MESSAGE" <<'"'"'SQL'"'"'
INSERT INTO system_operation_runs (
  id, operation_type, status, initiated_by, source, metadata_json,
  error_code, error_message, started_at, finished_at
)
VALUES (
  :'"'"'operation_id'"'"'::uuid,
  :'"'"'operation_type'"'"',
  :'"'"'operation_status'"'"',
  '"'"'host-operator'"'"',
  '"'"'host-recovery-cli'"'"',
  :'"'"'operation_metadata'"'"'::json,
  NULLIF(:'"'"'operation_error_code'"'"', '"'"''"'"'),
  NULLIF(:'"'"'operation_error_message'"'"', '"'"''"'"'),
  :'"'"'operation_started_at'"'"'::timestamptz,
  clock_timestamp()
);
SQL
    ' >/dev/null 2>&1; then
    warn "System operation history is unavailable; the command result is unchanged"
  fi
  return 0
}


validate_common_prerequisites() {
  ((BASH_VERSINFO[0] >= 4)) \
    || die "${EXIT_PREREQUISITE}" "E316" "Bash 4 or newer is required"
  require_command docker
  require_command python3
  python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' \
    || die "${EXIT_PREREQUISITE}" "E317" "Python 3.10 or newer is required"
  require_command flock
  require_command mktemp
  require_command date
  require_command grep
  require_command sed
  require_command tr
  require_command sleep
  require_command chmod
  require_command mkdir
  require_command mv
  require_command rm
  [[ -f "${MANIFEST_HELPER}" ]] \
    || die "${EXIT_PREREQUISITE}" "E302" "Recovery metadata helper is missing: ${MANIFEST_HELPER}"
  [[ -r "${ENV_FILE}" && ! -L "${ENV_FILE}" ]] \
    || die "${EXIT_PREREQUISITE}" "E303" "Environment file must be a readable regular file, not a symlink: ${ENV_FILE}"
  local compose_file
  for compose_file in "${COMPOSE_FILES[@]}"; do
    [[ -r "${compose_file}" && ! -L "${compose_file}" ]] \
      || die "${EXIT_PREREQUISITE}" "E304" "Compose file must be readable and not a symlink: ${compose_file}"
  done
  validate_project_name
  build_compose_command
  docker compose version >/dev/null 2>&1 \
    || die "${EXIT_PREREQUISITE}" "E305" "Docker Compose v2 is required"
  docker info >/dev/null 2>&1 \
    || die "${EXIT_PREREQUISITE}" "E306" "Docker daemon is unavailable"
}


require_compose_services() {
  local available_services
  if ! available_services="$(compose config --services)"; then
    die "${EXIT_PREREQUISITE}" "E307" "Compose configuration could not be rendered"
  fi
  local required_service
  for required_service in "$@"; do
    if ! grep -Fxq -- "${required_service}" <<<"${available_services}"; then
      die "${EXIT_PREREQUISITE}" "E308" "Required Compose service is missing: ${required_service}"
    fi
  done
}


require_running_service() {
  local service="$1"
  local running_services
  if ! running_services="$(compose ps --services --status running)"; then
    die "${EXIT_DATABASE}" "E501" "Unable to inspect running Compose services"
  fi
  if ! grep -Fxq -- "${service}" <<<"${running_services}"; then
    die "${EXIT_DATABASE}" "E502" "Compose service '${service}' must already be running"
  fi
}


database_scalar() {
  local sql="$1"
  compose exec -T db sh -ceu '
    exec psql --no-psqlrc --set=ON_ERROR_STOP=1 --tuples-only --no-align \
      --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --command "$1"
  ' sh "${sql}" | tr -d '\r' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'
}


require_database_ready() {
  if ! compose exec -T db sh -ceu '
    exec pg_isready --quiet --username "$POSTGRES_USER" --dbname "$POSTGRES_DB"
  '; then
    die "${EXIT_DATABASE}" "E503" "PostgreSQL is not ready for an online backup"
  fi
  if ! compose exec -T db pg_dump --version >/dev/null; then
    die "${EXIT_PREREQUISITE}" "E309" "pg_dump is unavailable in Compose service 'db'"
  fi
  if ! compose exec -T db pg_restore --version >/dev/null; then
    die "${EXIT_PREREQUISITE}" "E310" "pg_restore is unavailable in Compose service 'db'"
  fi
}


resolve_app_version() {
  local app_version="${APP_VERSION_OVERRIDE}"
  if [[ -z "${app_version}" && -r "${REPOSITORY_ROOT}/VERSION" ]]; then
    app_version="$(<"${REPOSITORY_ROOT}/VERSION")"
  fi
  if [[ -z "${app_version}" || "${app_version}" == *$'\n'* || "${app_version}" == *$'\r'* ]]; then
    die "${EXIT_PREREQUISITE}" "E311" "Application version is unavailable; set --app-version"
  fi
  printf '%s\n' "${app_version}"
}


resolve_database_image() {
  local image
  if ! image="$(compose config --format json | python3 "${MANIFEST_HELPER}" compose-image --service db)"; then
    die "${EXIT_PREREQUISITE}" "E312" "Unable to resolve the PostgreSQL image from Compose service 'db'"
  fi
  if ! docker image inspect "${image}" >/dev/null 2>&1; then
    die "${EXIT_PREREQUISITE}" "E313" \
      "PostgreSQL image '${image}' is not local; run docker compose pull db first"
  fi
  printf '%s\n' "${image}"
}


prepare_verified_archive() {
  local backup="$1"
  local expected_version="${2:-}"
  local -a inspect_args=(inspect --backup "${backup}")
  if [[ -n "${expected_version}" ]]; then
    inspect_args+=(--expected-app-version "${expected_version}")
  fi
  local inspection
  if ! inspection="$(python3 "${MANIFEST_HELPER}" "${inspect_args[@]}")"; then
    die "${EXIT_VALIDATION}" "E401" "Backup manifest, path, size, or checksum validation failed"
  fi
  local -a fields=()
  mapfile -t fields <<<"${inspection}"
  ((${#fields[@]} == 7)) \
    || die "${EXIT_VALIDATION}" "E406" "Validated backup inspection returned an unexpected result"
  VERIFIED_MANIFEST="${fields[0]}"
  VERIFIED_ARCHIVE="${fields[1]}"
  VERIFIED_APP_VERSION="${fields[2]}"
  VERIFIED_ALEMBIC_REVISION="${fields[3]}"
  VERIFIED_ARCHIVE_SHA256="${fields[4]}"
  VERIFIED_ARCHIVE_SIZE="${fields[5]}"
}


check_pg_restore_catalog() {
  local archive="$1"
  local database_image="$2"
  if ! docker run --rm --network none --entrypoint pg_restore "${database_image}" --version >/dev/null; then
    die "${EXIT_PREREQUISITE}" "E314" "pg_restore could not run from image '${database_image}'"
  fi
  if ! docker run --rm --interactive --network none --entrypoint pg_restore \
    "${database_image}" --list <"${archive}" >/dev/null; then
    die "${EXIT_VALIDATION}" "E402" "pg_restore could not read the custom-format archive catalog"
  fi
}


acquire_backup_lock() {
  local output_directory="$1"
  local lock_file="${output_directory}/.threatlens-recovery.lock"
  exec {BACKUP_LOCK_FD}>"${lock_file}"
  chmod 0600 "${lock_file}"
  if ! flock --nonblock "${BACKUP_LOCK_FD}"; then
    die "${EXIT_DATABASE}" "E504" "Another recovery operation holds the backup-directory lock"
  fi
}


release_backup_lock() {
  if [[ -n "${BACKUP_LOCK_FD}" ]]; then
    flock --unlock "${BACKUP_LOCK_FD}" || true
    exec {BACKUP_LOCK_FD}>&-
    BACKUP_LOCK_FD=""
  fi
}


perform_backup() {
  local output_directory="$1"
  [[ ! -L "${output_directory}" ]] \
    || die "${EXIT_VALIDATION}" "E403" "Backup output directory must not be a symlink"
  mkdir -p -- "${output_directory}" \
    || die "${EXIT_DATABASE}" "E517" "Unable to create the backup output directory"
  chmod 0700 "${output_directory}" \
    || die "${EXIT_DATABASE}" "E518" "Unable to restrict backup output-directory permissions"
  python3 "${MANIFEST_HELPER}" fsync-directory --path "${output_directory}" >/dev/null \
    || die "${EXIT_VALIDATION}" "E404" "Backup output directory is not a safe recovery path"
  acquire_backup_lock "${output_directory}"

  local partial
  for partial in "${output_directory}"/.threatlens-backup.partial.*; do
    if [[ -e "${partial}" ]]; then
      warn "Ignoring an incomplete backup directory from an interrupted run: ${partial}"
    fi
  done

  require_compose_services db
  require_running_service db
  require_database_ready

  local app_version alembic_revision postgresql_version snapshot_time database_size
  local estimated_counts encryption_fingerprint metadata_time compact_time random_suffix
  app_version="$(resolve_app_version)"
  alembic_revision="$(database_scalar \
    "SELECT string_agg(version_num, ',' ORDER BY version_num) FROM alembic_version;")" \
    || die "${EXIT_DATABASE}" "E505" "Unable to read the Alembic revision"
  [[ -n "${alembic_revision}" ]] \
    || die "${EXIT_DATABASE}" "E506" "Database has no Alembic revision and cannot produce a complete backup manifest"
  postgresql_version="$(database_scalar "SHOW server_version;")" \
    || die "${EXIT_DATABASE}" "E507" "Unable to read the PostgreSQL version"
  snapshot_time="$(database_scalar \
    "SELECT to_char(clock_timestamp() AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"');")" \
    || die "${EXIT_DATABASE}" "E508" "Unable to read the UTC database time"
  database_size="$(database_scalar "SELECT pg_database_size(current_database());")" \
    || die "${EXIT_DATABASE}" "E509" "Unable to read the PostgreSQL database size"
  estimated_counts="$(database_scalar \
    "SELECT COALESCE(json_object_agg(relname, GREATEST(n_live_tup, 0) ORDER BY relname), '{}'::json)::text FROM pg_stat_user_tables WHERE schemaname = 'public' AND relname IN ('users', 'feeds', 'items', 'integration_instances', 'integration_events', 'integration_deliveries', 'reports', 'report_schedules', 'audit_logs');")" \
    || die "${EXIT_DATABASE}" "E510" "Unable to collect safe table-count estimates"

  encryption_fingerprint="$(
    python3 "${MANIFEST_HELPER}" fingerprint-env --env-file "${ENV_FILE}"
  )" || die "${EXIT_PREREQUISITE}" "E315" "Unable to derive the non-secret encryption-key fingerprint"

  compact_time="$(date -u +'%Y%m%dT%H%M%SZ')"
  random_suffix="$(python3 -c 'import secrets; print(secrets.token_hex(4))')"
  local final_name="threatlens-postgresql-${compact_time}-${random_suffix}"
  local final_directory="${output_directory}/${final_name}"
  [[ ! -e "${final_directory}" && ! -L "${final_directory}" ]] \
    || die "${EXIT_DATABASE}" "E511" "Generated backup destination already exists"

  TEMP_BACKUP_DIRECTORY="$(mktemp -d -- "${output_directory}/.threatlens-backup.partial.XXXXXXXX")" \
    || die "${EXIT_DATABASE}" "E519" "Unable to create a temporary backup directory"
  chmod 0700 "${TEMP_BACKUP_DIRECTORY}" \
    || die "${EXIT_DATABASE}" "E520" "Unable to restrict temporary backup permissions"
  local archive_partial="${TEMP_BACKUP_DIRECTORY}/${ARCHIVE_FILENAME}.partial"
  local archive_path="${TEMP_BACKUP_DIRECTORY}/${ARCHIVE_FILENAME}"
  local manifest_path="${TEMP_BACKUP_DIRECTORY}/${MANIFEST_FILENAME}"

  log "Creating an online PostgreSQL custom-format backup"
  if ! compose exec -T db sh -ceu '
    exec pg_dump --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
      --format=custom --compress=6 --no-owner --no-acl
  ' >"${archive_partial}"; then
    die "${EXIT_DATABASE}" "E512" "pg_dump failed; no completed backup was published"
  fi
  chmod 0600 "${archive_partial}" \
    || die "${EXIT_DATABASE}" "E521" "Unable to restrict archive permissions"
  [[ -s "${archive_partial}" ]] \
    || die "${EXIT_DATABASE}" "E513" "pg_dump produced an empty archive"
  mv -- "${archive_partial}" "${archive_path}" \
    || die "${EXIT_DATABASE}" "E522" "Unable to atomically publish the archive inside its temporary directory"
  python3 "${MANIFEST_HELPER}" fsync --path "${archive_path}" >/dev/null \
    || die "${EXIT_DATABASE}" "E514" "Unable to make the archive durable"

  metadata_time="$(timestamp)"
  local -a manifest_args=(
    create
    --archive "${archive_path}"
    --output "${manifest_path}"
    --app-version "${app_version}"
    --alembic-revision "${alembic_revision}"
    --postgresql-version "${postgresql_version}"
    --snapshot-time-utc "${snapshot_time}"
    --metadata-collected-at-utc "${metadata_time}"
    --database-size-bytes "${database_size}"
    --estimated-counts-json "${estimated_counts}"
  )
  if [[ "${encryption_fingerprint}" != "none" ]]; then
    manifest_args+=(--encryption-key-fingerprint "${encryption_fingerprint}")
  fi
  python3 "${MANIFEST_HELPER}" "${manifest_args[@]}" \
    || die "${EXIT_VALIDATION}" "E405" "Backup manifest creation failed"
  chmod 0600 "${archive_path}" "${manifest_path}" \
    || die "${EXIT_DATABASE}" "E523" "Unable to restrict completed backup-file permissions"
  python3 "${MANIFEST_HELPER}" fsync-directory --path "${TEMP_BACKUP_DIRECTORY}" >/dev/null \
    || die "${EXIT_DATABASE}" "E515" "Unable to make the backup directory durable"

  mv -- "${TEMP_BACKUP_DIRECTORY}" "${final_directory}" \
    || die "${EXIT_DATABASE}" "E524" "Unable to atomically publish the completed backup directory"
  TEMP_BACKUP_DIRECTORY=""
  python3 "${MANIFEST_HELPER}" fsync-directory --path "${output_directory}" >/dev/null \
    || die "${EXIT_DATABASE}" "E516" "Backup completed but parent-directory durability could not be confirmed"
  release_backup_lock
  COMPLETED_BACKUP_DIRECTORY="${final_directory}"
  log "Backup completed atomically: ${final_directory}"
  printf '%s\n' "${final_directory}"
}


command_backup() {
  local output_directory="${THREATLENS_BACKUP_DIR:-${REPOSITORY_ROOT}/backups}"
  while (($#)); do
    case "$1" in
      --output-dir)
        (($# >= 2)) || die "${EXIT_USAGE}" "E202" "--output-dir requires a path"
        output_directory="$2"
        shift 2
        ;;
      --help)
        usage
        exit 0
        ;;
      *) die "${EXIT_USAGE}" "E203" "Unknown backup option: $1" ;;
    esac
  done
  perform_backup "${output_directory}"
  prepare_verified_archive "${COMPLETED_BACKUP_DIRECTORY}"
  set_operation_metadata \
    --field tool_version=1 \
    --field "archive_sha256=${VERIFIED_ARCHIVE_SHA256}" \
    --field "app_version=${VERIFIED_APP_VERSION}" \
    --field "alembic_revision=${VERIFIED_ALEMBIC_REVISION}" \
    --field "archive_size_bytes=${VERIFIED_ARCHIVE_SIZE}" \
    --field redis_restored=false
}


command_verify() {
  local backup=""
  local expected_version=""
  while (($#)); do
    case "$1" in
      --backup)
        (($# >= 2)) || die "${EXIT_USAGE}" "E204" "--backup requires a path"
        backup="$2"
        shift 2
        ;;
      --expected-app-version)
        (($# >= 2)) || die "${EXIT_USAGE}" "E205" "--expected-app-version requires a value"
        expected_version="$2"
        shift 2
        ;;
      --help)
        usage
        exit 0
        ;;
      *) die "${EXIT_USAGE}" "E206" "Unknown verify option: $1" ;;
    esac
  done
  [[ -n "${backup}" ]] || die "${EXIT_USAGE}" "E207" "verify requires --backup"

  prepare_verified_archive "${backup}" "${expected_version}"
  local database_image
  database_image="$(resolve_database_image)"
  check_pg_restore_catalog "${VERIFIED_ARCHIVE}" "${database_image}"
  set_operation_metadata \
    --field tool_version=1 \
    --field "archive_sha256=${VERIFIED_ARCHIVE_SHA256}" \
    --field "app_version=${VERIFIED_APP_VERSION}" \
    --field "alembic_revision=${VERIFIED_ALEMBIC_REVISION}" \
    --field catalog_checked=true \
    --field redis_restored=false
  log "Archive verification passed; this did not restore the database"
  printf 'VERIFY_STATUS=passed\nARCHIVE_SHA256=%s\n' "${VERIFIED_ARCHIVE_SHA256}"
}


wait_for_drill_database() {
  local timeout_seconds="$1"
  local deadline=$((SECONDS + timeout_seconds))
  while ((SECONDS < deadline)); do
    if docker exec "${DRILL_CONTAINER}" pg_isready --quiet --username postgres --dbname "${DRILL_DATABASE}"; then
      return 0
    fi
    if [[ "$(docker inspect --format '{{.State.Running}}' "${DRILL_CONTAINER}" 2>/dev/null || true)" != "true" ]]; then
      return 1
    fi
    sleep 1
  done
  return 1
}


command_drill() {
  local backup=""
  local timeout_seconds=90
  while (($#)); do
    case "$1" in
      --backup)
        (($# >= 2)) || die "${EXIT_USAGE}" "E208" "--backup requires a path"
        backup="$2"
        shift 2
        ;;
      --timeout-seconds)
        (($# >= 2)) || die "${EXIT_USAGE}" "E209" "--timeout-seconds requires a value"
        timeout_seconds="$2"
        shift 2
        ;;
      --help)
        usage
        exit 0
        ;;
      *) die "${EXIT_USAGE}" "E210" "Unknown drill option: $1" ;;
    esac
  done
  [[ -n "${backup}" ]] || die "${EXIT_USAGE}" "E211" "drill requires --backup"
  [[ "${timeout_seconds}" =~ ^[0-9]+$ ]] \
    || die "${EXIT_USAGE}" "E212" "--timeout-seconds must be an integer"
  ((timeout_seconds >= 10 && timeout_seconds <= 600)) \
    || die "${EXIT_USAGE}" "E213" "--timeout-seconds must be between 10 and 600"

  prepare_verified_archive "${backup}"
  local database_image expected_revision archive_checksum resource_suffix
  database_image="$(resolve_database_image)"
  check_pg_restore_catalog "${VERIFIED_ARCHIVE}" "${database_image}"
  expected_revision="${VERIFIED_ALEMBIC_REVISION}"
  archive_checksum="${VERIFIED_ARCHIVE_SHA256}"
  resource_suffix="$(python3 -c 'import secrets; print(secrets.token_hex(5))')"
  DRILL_NETWORK="threatlens-recovery-net-${resource_suffix}"
  DRILL_VOLUME="threatlens-recovery-db-${resource_suffix}"
  DRILL_CONTAINER="threatlens-recovery-pg-${resource_suffix}"

  log "Creating isolated restore-drill network and PostgreSQL target"
  docker network create --internal --label threatlens.recovery=restore-drill \
    "${DRILL_NETWORK}" >/dev/null \
    || die "${EXIT_DRILL}" "E601" "Unable to create the isolated drill network"
  docker volume create --label threatlens.recovery=restore-drill \
    "${DRILL_VOLUME}" >/dev/null \
    || die "${EXIT_DRILL}" "E602" "Unable to create the drill data volume"
  docker run --detach --name "${DRILL_CONTAINER}" --network "${DRILL_NETWORK}" \
    --mount "type=volume,source=${DRILL_VOLUME},target=/var/lib/postgresql/data" \
    --env POSTGRES_HOST_AUTH_METHOD=trust --env "POSTGRES_DB=${DRILL_DATABASE}" \
    --label threatlens.recovery=restore-drill "${database_image}" >/dev/null \
    || die "${EXIT_DRILL}" "E603" "Unable to start the isolated PostgreSQL drill target"

  wait_for_drill_database "${timeout_seconds}" \
    || die "${EXIT_DRILL}" "E604" "Isolated PostgreSQL did not become ready before the timeout"
  log "Restoring archive into the isolated target; no ThreatLens application service is running"
  if ! docker exec --interactive "${DRILL_CONTAINER}" pg_restore \
    --username postgres --dbname "${DRILL_DATABASE}" --exit-on-error \
    --single-transaction --no-owner --no-privileges <"${VERIFIED_ARCHIVE}"; then
    die "${EXIT_DRILL}" "E605" "Archive restore failed in the isolated target"
  fi

  local actual_revision table_count invalid_constraint_count smoke_value
  if ! actual_revision="$(docker exec "${DRILL_CONTAINER}" psql --no-psqlrc \
    --set=ON_ERROR_STOP=1 --tuples-only --no-align --username postgres \
    --dbname "${DRILL_DATABASE}" --command \
    "SELECT string_agg(version_num, ',' ORDER BY version_num) FROM alembic_version;" | tr -d '\r' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"; then
    die "${EXIT_DRILL}" "E606" "Restored Alembic revision could not be read"
  fi
  [[ "${actual_revision}" == "${expected_revision}" ]] \
    || die "${EXIT_DRILL}" "E607" \
      "Restored Alembic revision differs from manifest: expected=${expected_revision} actual=${actual_revision}"

  table_count="$(docker exec "${DRILL_CONTAINER}" psql --no-psqlrc \
    --set=ON_ERROR_STOP=1 --tuples-only --no-align --username postgres \
    --dbname "${DRILL_DATABASE}" --command \
    "SELECT count(*) FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p');" | tr -d '[:space:]')" \
    || die "${EXIT_DRILL}" "E608" "Restored schema table count could not be read"
  [[ "${table_count}" =~ ^[1-9][0-9]*$ ]] \
    || die "${EXIT_DRILL}" "E609" "Restored schema contains no application tables"

  invalid_constraint_count="$(docker exec "${DRILL_CONTAINER}" psql --no-psqlrc \
    --set=ON_ERROR_STOP=1 --tuples-only --no-align --username postgres \
    --dbname "${DRILL_DATABASE}" --command \
    "SELECT count(*) FROM pg_catalog.pg_constraint WHERE NOT convalidated;" | tr -d '[:space:]')" \
    || die "${EXIT_DRILL}" "E610" "Restored constraint validation state could not be read"
  [[ "${invalid_constraint_count}" == "0" ]] \
    || die "${EXIT_DRILL}" "E611" "Restored schema has ${invalid_constraint_count} unvalidated constraints"

  smoke_value="$(docker exec "${DRILL_CONTAINER}" psql --no-psqlrc \
    --set=ON_ERROR_STOP=1 --tuples-only --no-align --username postgres \
    --dbname "${DRILL_DATABASE}" --command \
    "SELECT 1 FROM users LIMIT 1;" | tr -d '[:space:]')" \
    || die "${EXIT_DRILL}" "E612" "Restored application-table smoke query failed"
  if [[ -n "${smoke_value}" && "${smoke_value}" != "1" ]]; then
    die "${EXIT_DRILL}" "E613" "Restored application-table smoke query returned an unexpected value"
  fi

  if ! cleanup_drill_resources; then
    die "${EXIT_DRILL}" "E614" "Drill passed, but one or more isolated Docker resources could not be removed"
  fi
  set_operation_metadata \
    --field tool_version=1 \
    --field "archive_sha256=${archive_checksum}" \
    --field "alembic_revision=${actual_revision}" \
    --field "table_count=${table_count}" \
    --field catalog_checked=true \
    --field redis_restored=false
  log "True isolated restore drill passed and all temporary resources were removed"
  printf 'RESTORE_DRILL_STATUS=passed\nARCHIVE_SHA256=%s\nALEMBIC_REVISION=%s\nTABLE_COUNT=%s\n' \
    "${archive_checksum}" "${actual_revision}" "${table_count}"
}


# shellcheck source=scripts/recovery/recovery_restore_lib.sh
source "${SCRIPT_DIR}/recovery_restore_lib.sh"


parse_global_options() {
  if [[ -n "${THREATLENS_COMPOSE_FILE:-}" ]]; then
    local compose_path_text="${THREATLENS_COMPOSE_FILE//:/$'\n'}"
    mapfile -t COMPOSE_FILES <<<"${compose_path_text}"
  else
    COMPOSE_FILES=("${REPOSITORY_ROOT}/docker-compose.yml")
  fi

  while (($#)); do
    case "$1" in
      --compose-file)
        (($# >= 2)) || die "${EXIT_USAGE}" "E220" "--compose-file requires a path"
        if [[ "${COMPOSE_FILES[*]}" == "${REPOSITORY_ROOT}/docker-compose.yml" ]]; then
          COMPOSE_FILES=()
        fi
        COMPOSE_FILES+=("$2")
        shift 2
        ;;
      --env-file)
        (($# >= 2)) || die "${EXIT_USAGE}" "E221" "--env-file requires a path"
        ENV_FILE="$2"
        shift 2
        ;;
      --project-name)
        (($# >= 2)) || die "${EXIT_USAGE}" "E222" "--project-name requires a value"
        COMPOSE_PROJECT="$2"
        shift 2
        ;;
      --app-version)
        (($# >= 2)) || die "${EXIT_USAGE}" "E223" "--app-version requires a value"
        APP_VERSION_OVERRIDE="$2"
        shift 2
        ;;
      --help|-h)
        usage
        exit 0
        ;;
      backup|verify|drill|restore)
        COMMAND="$1"
        shift
        COMMAND_ARGUMENTS=("$@")
        return 0
        ;;
      *) die "${EXIT_USAGE}" "E224" "Unknown global option or command: $1" ;;
    esac
  done
  die "${EXIT_USAGE}" "E225" "A command is required"
}


main() {
  declare -g COMMAND=""
  declare -ga COMMAND_ARGUMENTS=()
  parse_global_options "$@"
  validate_common_prerequisites
  start_operation "${COMMAND}"
  case "${COMMAND}" in
    backup) command_backup "${COMMAND_ARGUMENTS[@]}" ;;
    verify) command_verify "${COMMAND_ARGUMENTS[@]}" ;;
    drill) command_drill "${COMMAND_ARGUMENTS[@]}" ;;
    restore) tlr_restore_command "${COMMAND_ARGUMENTS[@]}" ;;
    *) die "${EXIT_USAGE}" "E226" "Unsupported command: ${COMMAND}" ;;
  esac
  finish_operation_best_effort succeeded
}


main "$@"
