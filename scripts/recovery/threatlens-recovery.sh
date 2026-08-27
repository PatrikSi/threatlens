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
readonly ARCHIVE_FILENAME="database.dump"
readonly MANIFEST_FILENAME="manifest.json"
readonly DRILL_DATABASE="threatlens_restore_drill"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPOSITORY_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd -P)"
MANIFEST_HELPER="${SCRIPT_DIR}/recovery_manifest.py"
SAFETY_HELPER="${SCRIPT_DIR}/recovery_safety.py"
JOURNAL_HELPER="${SCRIPT_DIR}/recovery_journal.py"

declare -a COMPOSE_FILES=()
declare -a COMPOSE_COMMAND=()
ENV_FILE="${THREATLENS_ENV_FILE:-${REPOSITORY_ROOT}/.env}"
COMPOSE_PROJECT="${THREATLENS_COMPOSE_PROJECT:-}"
APP_VERSION_OVERRIDE="${THREATLENS_APP_VERSION:-}"
RECOVERY_JOURNAL_ROOT="${THREATLENS_RECOVERY_JOURNAL_DIR:-${REPOSITORY_ROOT}/backups/recovery-journal}"

TEMP_BACKUP_DIRECTORY=""
DRILL_CONTAINER=""
DRILL_APP_CONTAINER=""
DRILL_VOLUME=""
DRILL_NETWORK=""
DRILL_TABLE_COUNT=""
DRILL_UPGRADED_REVISION=""
RECOVERY_APP_CONTAINER=""
RECOVERY_NETWORK=""
RECOVERY_DATABASE_CONTAINER_ATTACHED=""
BACKUP_LOCK_DIRECTORY=""
COMPLETED_BACKUP_DIRECTORY=""
RESTORE_STAGE_DIRECTORY=""
RUNTIME_VALIDATION_DIRECTORY=""
STAGED_QUARANTINE_HOOK=""
STAGED_MANIFEST_HELPER=""
STAGED_ARCHIVE_SHA256=""
STAGED_HOOK_SHA256=""
STAGED_HELPER_SHA256=""
STAGED_MANIFEST_SHA256=""
VERIFIED_MANIFEST=""
VERIFIED_ARCHIVE=""
VERIFIED_APP_VERSION=""
VERIFIED_ALEMBIC_REVISION=""
VERIFIED_ARCHIVE_SHA256=""
VERIFIED_ARCHIVE_SIZE=""
VERIFIED_ENCRYPTION_FINGERPRINT=""
SUPPORTED_DATABASE_NAME=""
SUPPORTED_REDIS_DATABASE=""
TARGET_CONFIG_SHA256=""
TARGET_DEPLOYMENT_IDENTITY=""
OPERATION_TYPE=""
OPERATION_STARTED_AT=""
OPERATION_METADATA_JSON='{"tool_version":"1"}'
OPERATION_LEDGER_ALLOWED=false
OPERATION_FINISHED=false
CONTROLLED_EXIT=false
RESTORE_REPLACEMENT_ACTIVE=false
RESTORE_ROLLBACK_DATABASE=""
RESTORE_PHASE="idle"
RESTORE_RECOVERY_ROLE=""
RESTORE_RECOVERY_PASSWORD=""
RESTORE_ORIGINAL_ROLE_CAN_LOGIN=""
RESTORE_ORIGINAL_DATABASE_ALLOW_CONNECTIONS=""
RESTORE_ORIGINAL_DATABASE_OID=""
RESTORE_REPLACEMENT_DATABASE_OID=""
RESTORE_JOURNAL_ACTIVE=false
RESTORE_JOURNAL_STATUS=""
RESTORE_JOURNAL_OUTCOME=""
RESTORE_JOURNAL_ERROR_CODE=""
RESTORE_JOURNAL_TARGET_CONFIG_SHA256=""
RECOVERY_OPERATION_LOCK_FD=""
RESTORE_FORWARD_COMMITTED=false
RESTORE_RECONCILED_ROLLBACK=false
OPERATION_ID=""
OPERATION_EVIDENCE_RECORDED=false
OPERATION_SCOPE_ID=""
PINNED_DATABASE_IMAGE=""
PINNED_APPLICATION_IMAGE=""
TARGET_BACKEND_SERVICES_TEXT=""


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
  if [[ "${RESTORE_REPLACEMENT_ACTIVE:-false}" == true ]]; then
    _tlr_restore_emergency_rollback || true
  fi
  if [[ "${RESTORE_FORWARD_COMMITTED:-false}" == true ]]; then
    set_operation_metadata \
      --field tool_version=1 \
      --field "archive_sha256=${VERIFIED_ARCHIVE_SHA256}" \
      --field outbound_quarantined=true \
      --field redis_restored=false \
      --field reconciled_after_interruption=true \
      --field reconciled_forward=true
    if ! _tlr_restore_journal_terminal succeeded forward_committed ""; then
      OPERATION_LEDGER_ALLOWED=false
      warn "Forward commit was proved, but its terminal host journal could not be persisted; services remain stopped"
      exit "${EXIT_RESTORE}"
    fi
    finish_operation_best_effort succeeded
    archive_restore_journal_best_effort
    printf 'RESTORE_STATUS=completed_quarantined_after_reconciliation\n'
    exit 0
  fi
  if [[ "${RESTORE_RECONCILED_ROLLBACK:-false}" == true ]]; then
    if ! _tlr_restore_journal_terminal failed rolled_back "${error_code}"; then
      OPERATION_LEDGER_ALLOWED=false
      warn "Rollback was proved, but its terminal host journal could not be persisted; services remain stopped"
      exit "${EXIT_RESTORE}"
    fi
  fi
  finish_operation_best_effort failed "${error_code}"
  archive_restore_journal_best_effort
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
  threatlens-recovery.sh [global options] reconcile

Global options:
  --compose-file PATH    Compose file; repeat for overrides (default: docker-compose.yml)
  --env-file PATH        Compose environment file (default: repository .env)
  --project-name NAME    Explicit Compose project name
  --app-version VERSION  Override the repository VERSION value in backup metadata
  --journal-dir PATH     Durable private destructive-recovery journal root
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
  --quarantine-hook PATH Executable hook to preflight against the restored copy
  --acknowledge-encryption-key-mismatch
                         Permit a drill with a different encryption-key fingerprint

Restore options:
  --backup PATH          Completed backup directory or manifest.json
  --show-confirmation    Validate the target and print its target-bound confirmation
  --confirm TEXT         Exact confirmation printed by --show-confirmation
  --acknowledge-data-loss
                         Independent opt-in acknowledging destructive replacement
  --quarantine-hook PATH Executable application hook supporting preflight/apply/verify
  --safety-backup-dir PATH
                         Fresh pre-restore backup parent (default: ./backups/pre-restore)
  --allow-app-version-mismatch
                         Explicitly allow restore from another application version
  --acknowledge-encryption-key-mismatch
                         Allow a restore whose key fingerprint differs from this deployment

Environment equivalents:
  THREATLENS_COMPOSE_FILE (colon-separated), THREATLENS_ENV_FILE,
  THREATLENS_COMPOSE_PROJECT, THREATLENS_APP_VERSION,
  THREATLENS_BACKUP_DIR, THREATLENS_POST_RESTORE_HOOK,
  THREATLENS_RECOVERY_JOURNAL_DIR.

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
  if [[ -n "${DRILL_APP_CONTAINER}" ]]; then
    if ! docker rm --force "${DRILL_APP_CONTAINER}" >/dev/null 2>&1; then
      cleanup_failed=1
    fi
    DRILL_APP_CONTAINER=""
  fi
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


cleanup_recovery_network() {
  local cleanup_failed=0
  if [[ -n "${RECOVERY_APP_CONTAINER}" ]]; then
    if ! docker rm --force "${RECOVERY_APP_CONTAINER}" >/dev/null 2>&1; then
      cleanup_failed=1
    fi
    RECOVERY_APP_CONTAINER=""
  fi
  if [[ -n "${RECOVERY_DATABASE_CONTAINER_ATTACHED}" && -n "${RECOVERY_NETWORK}" ]]; then
    if ! docker network disconnect --force "${RECOVERY_NETWORK}" \
      "${RECOVERY_DATABASE_CONTAINER_ATTACHED}" >/dev/null 2>&1; then
      cleanup_failed=1
    fi
    RECOVERY_DATABASE_CONTAINER_ATTACHED=""
  fi
  if [[ -n "${RECOVERY_NETWORK}" ]]; then
    if ! docker network rm "${RECOVERY_NETWORK}" >/dev/null 2>&1; then
      cleanup_failed=1
    fi
    RECOVERY_NETWORK=""
  fi
  return "${cleanup_failed}"
}


cleanup_restore_stage() {
  if [[ -z "${RESTORE_STAGE_DIRECTORY}" ]]; then
    return 0
  fi
  local basename
  basename="$(basename -- "${RESTORE_STAGE_DIRECTORY}")"
  if [[ "${basename}" == threatlens-recovery-stage.* \
    && -d "${RESTORE_STAGE_DIRECTORY}" && ! -L "${RESTORE_STAGE_DIRECTORY}" ]]; then
    rm -rf -- "${RESTORE_STAGE_DIRECTORY}"
  else
    warn "Refusing automatic cleanup of unexpected restore staging path: ${RESTORE_STAGE_DIRECTORY}"
    return 1
  fi
  RESTORE_STAGE_DIRECTORY=""
}


cleanup_runtime_validation() {
  if [[ -z "${RUNTIME_VALIDATION_DIRECTORY}" ]]; then
    return 0
  fi
  local basename
  basename="$(basename -- "${RUNTIME_VALIDATION_DIRECTORY}")"
  if [[ "${basename}" == threatlens-runtime-validation.* \
    && -d "${RUNTIME_VALIDATION_DIRECTORY}" && ! -L "${RUNTIME_VALIDATION_DIRECTORY}" ]]; then
    rm -rf -- "${RUNTIME_VALIDATION_DIRECTORY}"
  else
    warn "Refusing automatic cleanup of unexpected runtime-validation path: ${RUNTIME_VALIDATION_DIRECTORY}"
    return 1
  fi
  RUNTIME_VALIDATION_DIRECTORY=""
}


acquire_recovery_operation_lock() {
  require_command flock
  local lock_path
  lock_path="$(python3 "${JOURNAL_HELPER}" prepare --root "${RECOVERY_JOURNAL_ROOT}")" \
    || die "${EXIT_REFUSED}" "E721" \
      "Recovery operation lock storage could not be prepared safely"
  exec {RECOVERY_OPERATION_LOCK_FD}>"${lock_path}" \
    || die "${EXIT_REFUSED}" "E722" "Recovery operation lock could not be opened"
  flock --nonblock "${RECOVERY_OPERATION_LOCK_FD}" \
    || die "${EXIT_REFUSED}" "E723" \
      "Another recovery operation is active for this journal root"
}


release_recovery_operation_lock() {
  if [[ -n "${RECOVERY_OPERATION_LOCK_FD}" ]]; then
    flock --unlock "${RECOVERY_OPERATION_LOCK_FD}" >/dev/null 2>&1 || true
    exec {RECOVERY_OPERATION_LOCK_FD}>&-
  fi
}


archive_restore_journal_best_effort() {
  if [[ "${RESTORE_JOURNAL_ACTIVE:-false}" != true \
    || "${OPERATION_EVIDENCE_RECORDED:-false}" != true ]]; then
    return 0
  fi
  if python3 "${JOURNAL_HELPER}" archive --root "${RECOVERY_JOURNAL_ROOT}" >/dev/null; then
    RESTORE_JOURNAL_ACTIVE=false
  else
    warn "Terminal recovery journal could not be archived; run reconcile after correcting journal access"
    return 1
  fi
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
  cleanup_recovery_network
  cleanup_restore_stage
  cleanup_runtime_validation
  release_backup_lock
  archive_restore_journal_best_effort
  release_recovery_operation_lock
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
    reconcile) OPERATION_TYPE="" ;;
    *) return 0 ;;
  esac
  OPERATION_STARTED_AT="$(timestamp)"
  OPERATION_ID="$(python3 -c 'import uuid; print(uuid.uuid4())')" \
    || die "${EXIT_PREREQUISITE}" "E328" "Unable to generate an operation identifier"
  OPERATION_SCOPE_ID="$(
    python3 -c \
      'import hashlib, os, sys; print(hashlib.sha256("\0".join((os.path.abspath(sys.argv[1]), sys.argv[2])).encode()).hexdigest())' \
      "${RECOVERY_JOURNAL_ROOT}" "${COMPOSE_PROJECT:-default}"
  )" || die "${EXIT_PREREQUISITE}" "E329" "Unable to derive the recovery ledger scope"
  OPERATION_METADATA_JSON="$(
    python3 "${MANIFEST_HELPER}" ledger-metadata \
      --field tool_version=1 \
      --field "ledger_scope_id=${OPERATION_SCOPE_ID}"
  )" || OPERATION_METADATA_JSON="{\"ledger_scope_id\":\"${OPERATION_SCOPE_ID}\",\"tool_version\":\"1\"}"
  OPERATION_LEDGER_ALLOWED=true
  if [[ "${OPERATION_TYPE}" == "restore" ]]; then
    OPERATION_LEDGER_ALLOWED=false
  fi
  OPERATION_FINISHED=false
  OPERATION_EVIDENCE_RECORDED=false
}


set_operation_metadata() {
  local metadata
  local -a metadata_arguments=("$@")
  if [[ -n "${OPERATION_SCOPE_ID}" ]]; then
    metadata_arguments+=(--field "ledger_scope_id=${OPERATION_SCOPE_ID}")
  fi
  if metadata="$(
    python3 "${MANIFEST_HELPER}" ledger-metadata "${metadata_arguments[@]}"
  )"; then
    OPERATION_METADATA_JSON="${metadata}"
  else
    warn "Operation metadata was invalid and was replaced with the minimal safe record"
    OPERATION_METADATA_JSON="{\"ledger_scope_id\":\"${OPERATION_SCOPE_ID}\",\"tool_version\":\"1\"}"
  fi
}


begin_operation_best_effort() {
  if [[ -z "${OPERATION_TYPE}" || "${OPERATION_LEDGER_ALLOWED}" != true \
    || ${#COMPOSE_COMMAND[@]} -eq 0 ]]; then
    return 0
  fi

  if ! compose exec -T \
    --env "THREATLENS_OPERATION_ID=${OPERATION_ID}" \
    --env "THREATLENS_OPERATION_TYPE=${OPERATION_TYPE}" \
    --env "THREATLENS_OPERATION_STATUS=running" \
    --env "THREATLENS_OPERATION_STARTED_AT=${OPERATION_STARTED_AT}" \
    --env "THREATLENS_OPERATION_METADATA=${OPERATION_METADATA_JSON}" \
    --env "THREATLENS_OPERATION_SCOPE_ID=${OPERATION_SCOPE_ID}" \
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
        --set=operation_started_at="$THREATLENS_OPERATION_STARTED_AT" \
        --set=operation_metadata="$THREATLENS_OPERATION_METADATA" \
        --set=operation_scope_id="$THREATLENS_OPERATION_SCOPE_ID" <<'"'"'SQL'"'"'
BEGIN;
UPDATE system_operation_runs
SET status = '"'"'failed'"'"',
    metadata_json = jsonb_set(
      metadata_json,
      '"'"'{reconciled_after_interruption}'"'"',
      '"'"'true'"'"'::jsonb,
      true
    ),
    error_code = '"'"'operation_interrupted'"'"',
    error_message = '"'"'A later host recovery command reconciled this unfinished operation.'"'"',
    finished_at = clock_timestamp(),
    updated_at = clock_timestamp()
WHERE status = '"'"'running'"'"'
  AND source = '"'"'host-recovery-cli'"'"'
  AND operation_type IN ('"'"'backup'"'"', '"'"'verify'"'"', '"'"'restore_drill'"'"')
  AND id <> :'"'"'operation_id'"'"'::uuid
  AND metadata_json ->> '"'"'ledger_scope_id'"'"' = :'"'"'operation_scope_id'"'"';
INSERT INTO system_operation_runs (
  id, operation_type, status, initiated_by, source, metadata_json,
  error_code, error_message, started_at, finished_at
)
VALUES (
  :'"'"'operation_id'"'"'::uuid,
  :'"'"'operation_type'"'"',
  '"'"'running'"'"',
  '"'"'host-operator'"'"',
  '"'"'host-recovery-cli'"'"',
  :'"'"'operation_metadata'"'"'::jsonb,
  NULL,
  NULL,
  :'"'"'operation_started_at'"'"'::timestamptz,
  NULL
)
ON CONFLICT (id) DO NOTHING;
COMMIT;
SQL
  ' >/dev/null 2>&1; then
    warn "System operation start history is unavailable; the command result is unchanged"
  else
    OPERATION_EVIDENCE_RECORDED=true
  fi
  return 0
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

  local run_id="${OPERATION_ID}" error_message=""
  if [[ -z "${run_id}" ]]; then
    run_id="$(python3 -c 'import uuid; print(uuid.uuid4())' 2>/dev/null)" || {
      warn "System operation history could not be recorded; UUID generation failed"
      return 0
    }
  fi
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
  :'"'"'operation_metadata'"'"'::jsonb,
  NULLIF(:'"'"'operation_error_code'"'"', '"'"''"'"'),
  NULLIF(:'"'"'operation_error_message'"'"', '"'"''"'"'),
  :'"'"'operation_started_at'"'"'::timestamptz,
  clock_timestamp()
)
ON CONFLICT (id) DO UPDATE
SET status = EXCLUDED.status,
    metadata_json = EXCLUDED.metadata_json,
    error_code = EXCLUDED.error_code,
    error_message = EXCLUDED.error_message,
    finished_at = EXCLUDED.finished_at,
    updated_at = clock_timestamp()
WHERE system_operation_runs.status = '"'"'running'"'"';
SQL
  ' >/dev/null 2>&1; then
    warn "System operation history is unavailable; the command result is unchanged"
  else
    OPERATION_EVIDENCE_RECORDED=true
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
  require_command rmdir
  [[ -f "${MANIFEST_HELPER}" ]] \
    || die "${EXIT_PREREQUISITE}" "E302" "Recovery metadata helper is missing: ${MANIFEST_HELPER}"
  [[ -f "${SAFETY_HELPER}" ]] \
    || die "${EXIT_PREREQUISITE}" "E318" "Recovery safety helper is missing: ${SAFETY_HELPER}"
  [[ -f "${JOURNAL_HELPER}" && ! -L "${JOURNAL_HELPER}" ]] \
    || die "${EXIT_PREREQUISITE}" "E327" "Recovery journal helper is missing or unsafe: ${JOURNAL_HELPER}"
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


validate_supported_local_targets() {
  local validation
  if ! validation="$(compose config --format json | python3 "${SAFETY_HELPER}" validate-target)"; then
    die "${EXIT_REFUSED}" "E707" \
      "Rendered application data targets are unsupported; recovery currently requires the local Compose db and redis services with Redis database 0"
  fi
  local -a fields=()
  mapfile -t fields <<<"${validation}"
  ((${#fields[@]} == 5)) \
    || die "${EXIT_PREREQUISITE}" "E319" "Target validation returned an unexpected result"
  SUPPORTED_DATABASE_NAME="${fields[0]}"
  SUPPORTED_REDIS_DATABASE="${fields[2]}"
  TARGET_CONFIG_SHA256="${fields[3]}"
  TARGET_BACKEND_SERVICES_TEXT="${fields[4]}"
  [[ "${SUPPORTED_REDIS_DATABASE}" == "0" ]] \
    || die "${EXIT_REFUSED}" "E708" "Only Redis database 0 is supported by the local recovery adapter"
}


target_backend_services() {
  local service_text="${TARGET_BACKEND_SERVICES_TEXT//,/$'\n'}"
  [[ -n "${service_text}" ]] || return 0
  printf '%s\n' "${service_text}"
}


validate_running_target_configuration() {
  cleanup_runtime_validation || die "${EXIT_PREREQUISITE}" "E331" \
    "Unable to clean a previous runtime-validation directory"
  RUNTIME_VALIDATION_DIRECTORY="$(mktemp -d -- "${TMPDIR:-/tmp}/threatlens-runtime-validation.XXXXXXXX")" \
    || die "${EXIT_PREREQUISITE}" "E332" "Unable to create private runtime-validation storage"
  chmod 0700 "${RUNTIME_VALIDATION_DIRECTORY}" \
    || die "${EXIT_PREREQUISITE}" "E333" "Unable to restrict runtime-validation storage"
  local config_path="${RUNTIME_VALIDATION_DIRECTORY}/compose.json"
  local inspect_path="${RUNTIME_VALIDATION_DIRECTORY}/inspect.json"
  compose config --format json >"${config_path}" \
    || die "${EXIT_PREREQUISITE}" "E334" "Unable to render Compose configuration for runtime validation"
  chmod 0600 "${config_path}"

  local -a services=(db redis)
  local service
  while IFS= read -r service; do
    [[ -n "${service}" ]] && services+=("${service}")
  done < <(target_backend_services)
  local -a containers=()
  local container
  for service in "${services[@]}"; do
    container="$(compose ps --quiet --all "${service}")" \
      || die "${EXIT_PREREQUISITE}" "E335" "Unable to resolve runtime container for service '${service}'"
    if [[ -n "${container}" ]]; then
      [[ "${container}" != *$'\n'* ]] \
        || die "${EXIT_REFUSED}" "E713" "Compose service '${service}' resolves to multiple containers"
      containers+=("${container}")
    fi
  done
  ((${#containers[@]} >= 2)) \
    || die "${EXIT_REFUSED}" "E714" "Running db and redis containers are required for runtime validation"
  docker inspect "${containers[@]}" >"${inspect_path}" \
    || die "${EXIT_PREREQUISITE}" "E336" "Unable to inspect running recovery target containers"
  chmod 0600 "${inspect_path}"
  python3 "${SAFETY_HELPER}" validate-runtime \
    --compose-config "${config_path}" --inspect "${inspect_path}" >/dev/null \
    || die "${EXIT_REFUSED}" "E715" \
      "Running container data targets differ from rendered Compose configuration"
  cleanup_runtime_validation \
    || die "${EXIT_PREREQUISITE}" "E337" "Runtime-validation storage could not be removed"
}


resolve_compose_container() {
  local service="$1"
  local container container_project container_service
  container="$(compose ps --quiet "${service}")" \
    || die "${EXIT_DATABASE}" "E525" "Unable to resolve the running ${service} container"
  [[ -n "${container}" && "${container}" != *$'\n'* ]] \
    || die "${EXIT_DATABASE}" "E526" "Compose service '${service}' does not resolve to one container"
  container_project="$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}' \
    "${container}")" \
    || die "${EXIT_DATABASE}" "E529" "Unable to read the ${service} container project identity"
  container_service="$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.service" }}' \
    "${container}")" \
    || die "${EXIT_DATABASE}" "E530" "Unable to read the ${service} container service identity"
  [[ "${container_project}" == "${COMPOSE_PROJECT}" && "${container_service}" == "${service}" ]] \
    || die "${EXIT_REFUSED}" "E712" \
      "Running container identity does not match project '${COMPOSE_PROJECT}' service '${service}'"
  printf '%s\n' "${container}"
}


resolve_target_deployment_identity() {
  local archive_sha256="$1"
  local db_container redis_container db_identity redis_identity inspect_payload
  db_container="$(resolve_compose_container db)"
  redis_container="$(resolve_compose_container redis)"
  db_identity="$(docker inspect --format \
    '{{.Id}}|{{.Image}}|{{.Name}}|{{range .Mounts}}{{.Type}}:{{.Name}}:{{.Source}}:{{.Destination}};{{end}}' \
    "${db_container}")" \
    || die "${EXIT_DATABASE}" "E527" "Unable to inspect the live database container"
  redis_identity="$(docker inspect --format \
    '{{.Id}}|{{.Image}}|{{.Name}}|{{range .Mounts}}{{.Type}}:{{.Name}}:{{.Source}}:{{.Destination}};{{end}}' \
    "${redis_container}")" \
    || die "${EXIT_DATABASE}" "E531" "Unable to inspect the live Redis container"
  inspect_payload="database=${db_identity}"$'\n'"redis=${redis_identity}"
  TARGET_DEPLOYMENT_IDENTITY="$(
    printf '%s' "${inspect_payload}" | python3 "${SAFETY_HELPER}" identity \
      --project "${COMPOSE_PROJECT}" \
      --database "${SUPPORTED_DATABASE_NAME}" \
      --target-config-sha256 "${TARGET_CONFIG_SHA256}" \
      --archive-sha256 "${archive_sha256}"
  )" || die "${EXIT_PREREQUISITE}" "E320" "Unable to derive the target deployment identity"
  [[ "${TARGET_DEPLOYMENT_IDENTITY}" =~ ^[0-9a-f]{64}$ ]] \
    || die "${EXIT_PREREQUISITE}" "E321" "Target deployment identity is invalid"
}


revalidate_live_target_identity() {
  local expected_config_sha256="$1"
  local expected_deployment_identity="$2"
  local archive_sha256="$3"
  validate_supported_local_targets
  [[ "${TARGET_CONFIG_SHA256}" == "${expected_config_sha256}" ]] \
    || die "${EXIT_REFUSED}" "E716" \
      "Rendered recovery target configuration changed after confirmation"
  validate_running_target_configuration
  resolve_target_deployment_identity "${archive_sha256}"
  [[ "${TARGET_DEPLOYMENT_IDENTITY}" == "${expected_deployment_identity}" ]] \
    || die "${EXIT_REFUSED}" "E717" \
      "Live database or Redis container/image/mount identity changed after confirmation"
}


restore_confirmation_text() {
  printf 'RESTORE THREATLENS project=%s database=%s archive=%s deployment=%s\n' \
    "${COMPOSE_PROJECT}" "${SUPPORTED_DATABASE_NAME}" "${VERIFIED_ARCHIVE_SHA256}" \
    "${TARGET_DEPLOYMENT_IDENTITY}"
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
  docker image inspect --format '{{.Id}}' "${image}" \
    || die "${EXIT_PREREQUISITE}" "E329" \
      "Unable to pin PostgreSQL image '${image}' by immutable image ID"
}


resolve_application_image() {
  local image
  if ! image="$(compose config --format json | python3 "${MANIFEST_HELPER}" compose-image --service api)"; then
    die "${EXIT_PREREQUISITE}" "E322" "Unable to resolve the current backend image from Compose service 'api'"
  fi
  if ! docker image inspect "${image}" >/dev/null 2>&1; then
    die "${EXIT_PREREQUISITE}" "E323" \
      "Backend image '${image}' is not local; pull or build the deployment image before recovery testing"
  fi
  docker image inspect --format '{{.Id}}' "${image}" \
    || die "${EXIT_PREREQUISITE}" "E330" \
      "Unable to pin backend image '${image}' by immutable image ID"
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
  ((${#fields[@]} == 8)) \
    || die "${EXIT_VALIDATION}" "E406" "Validated backup inspection returned an unexpected result"
  VERIFIED_MANIFEST="${fields[0]}"
  VERIFIED_ARCHIVE="${fields[1]}"
  VERIFIED_APP_VERSION="${fields[2]}"
  VERIFIED_ALEMBIC_REVISION="${fields[3]}"
  VERIFIED_ARCHIVE_SHA256="${fields[4]}"
  VERIFIED_ARCHIVE_SIZE="${fields[5]}"
  VERIFIED_ENCRYPTION_FINGERPRINT="${fields[7]}"
}


require_encryption_fingerprint_match() {
  local acknowledge_mismatch="$1"
  local deployment_fingerprint
  deployment_fingerprint="$(
    compose config --format json | python3 "${SAFETY_HELPER}" encryption-fingerprint
  )" || die "${EXIT_PREREQUISITE}" "E324" \
    "Unable to derive the deployment encryption-key fingerprint"
  if [[ "${VERIFIED_ENCRYPTION_FINGERPRINT}" == "${deployment_fingerprint}" ]]; then
    return 0
  fi
  [[ "${acknowledge_mismatch}" == true ]] \
    || die "${EXIT_REFUSED}" "E709" \
      "Backup encryption-key fingerprint differs from this deployment; supply the matching key or explicitly acknowledge the mismatch"
  warn "Encryption-key fingerprint mismatch was explicitly acknowledged; encrypted restored values may be unreadable"
}


stage_restore_inputs() {
  local hook="$1"
  local manifest_sha256 hook_sha256 helper_sha256
  local approved_app_version="${VERIFIED_APP_VERSION}"
  local approved_alembic_revision="${VERIFIED_ALEMBIC_REVISION}"
  local approved_archive_sha256="${VERIFIED_ARCHIVE_SHA256}"
  local approved_archive_size="${VERIFIED_ARCHIVE_SIZE}"
  local approved_encryption_fingerprint="${VERIFIED_ENCRYPTION_FINGERPRINT}"
  manifest_sha256="$(python3 "${SAFETY_HELPER}" sha256 --path "${VERIFIED_MANIFEST}")" \
    || die "${EXIT_REFUSED}" "E710" "Unable to hash the approved recovery manifest"
  hook_sha256="$(python3 "${SAFETY_HELPER}" sha256 --path "${hook}")" \
    || die "${EXIT_REFUSED}" "E710" "Unable to hash the approved quarantine hook"
  helper_sha256="$(python3 "${SAFETY_HELPER}" sha256 --path "${MANIFEST_HELPER}")" \
    || die "${EXIT_REFUSED}" "E710" "Unable to hash the approved manifest helper"
  RESTORE_STAGE_DIRECTORY="$(mktemp -d -- "${TMPDIR:-/tmp}/threatlens-recovery-stage.XXXXXXXX")" \
    || die "${EXIT_PREREQUISITE}" "E325" "Unable to create a private restore staging directory"
  chmod 0700 "${RESTORE_STAGE_DIRECTORY}" \
    || die "${EXIT_PREREQUISITE}" "E326" "Unable to restrict restore staging permissions"

  local staging
  if ! staging="$(python3 "${SAFETY_HELPER}" stage \
    --manifest "${VERIFIED_MANIFEST}" \
    --archive "${VERIFIED_ARCHIVE}" \
    --hook "${hook}" \
    --manifest-helper "${MANIFEST_HELPER}" \
    --destination "${RESTORE_STAGE_DIRECTORY}" \
    --expected-manifest-sha256 "${manifest_sha256}" \
    --expected-archive-sha256 "${VERIFIED_ARCHIVE_SHA256}" \
    --expected-hook-sha256 "${hook_sha256}" \
    --expected-helper-sha256 "${helper_sha256}")"; then
    die "${EXIT_VALIDATION}" "E407" \
      "Restore inputs changed or could not be copied into private staging"
  fi
  local -a fields=()
  mapfile -t fields <<<"${staging}"
  ((${#fields[@]} == 9)) \
    || die "${EXIT_VALIDATION}" "E408" "Restore staging returned an unexpected result"
  VERIFIED_MANIFEST="${fields[0]}"
  VERIFIED_ARCHIVE="${fields[1]}"
  STAGED_QUARANTINE_HOOK="${fields[2]}"
  STAGED_MANIFEST_HELPER="${fields[3]}"
  STAGED_ARCHIVE_SHA256="${fields[4]}"
  STAGED_HOOK_SHA256="${fields[5]}"
  STAGED_HELPER_SHA256="${fields[6]}"
  STAGED_MANIFEST_SHA256="${fields[7]}"
  [[ "${fields[8]}" == "${VERIFIED_ARCHIVE_SIZE}" ]] \
    || die "${EXIT_VALIDATION}" "E409" "Staged archive size differs from the approved manifest"
  prepare_verified_archive "${VERIFIED_MANIFEST}"
  [[ "${VERIFIED_APP_VERSION}" == "${approved_app_version}" \
    && "${VERIFIED_ALEMBIC_REVISION}" == "${approved_alembic_revision}" \
    && "${VERIFIED_ARCHIVE_SHA256}" == "${approved_archive_sha256}" \
    && "${VERIFIED_ARCHIVE_SIZE}" == "${approved_archive_size}" \
    && "${VERIFIED_ENCRYPTION_FINGERPRINT}" == "${approved_encryption_fingerprint}" ]] \
    || die "${EXIT_VALIDATION}" "E410" \
      "Staged recovery metadata differs from the approved manifest"
}


revalidate_staged_restore_inputs() {
  local archive_sha hook_sha helper_sha manifest_sha
  prepare_verified_archive "${VERIFIED_MANIFEST}"
  archive_sha="${VERIFIED_ARCHIVE_SHA256}"
  hook_sha="$(python3 "${SAFETY_HELPER}" sha256 --path "${STAGED_QUARANTINE_HOOK}")" || return 1
  helper_sha="$(python3 "${SAFETY_HELPER}" sha256 --path "${STAGED_MANIFEST_HELPER}")" || return 1
  manifest_sha="$(python3 "${SAFETY_HELPER}" sha256 --path "${VERIFIED_MANIFEST}")" || return 1
  [[ "${archive_sha}" == "${STAGED_ARCHIVE_SHA256}" \
    && "${hook_sha}" == "${STAGED_HOOK_SHA256}" \
    && "${helper_sha}" == "${STAGED_HELPER_SHA256}" \
    && "${manifest_sha}" == "${STAGED_MANIFEST_SHA256}" ]]
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
  local lock_directory="${output_directory}/.threatlens-recovery.lock"
  if ! mkdir --mode=0700 -- "${lock_directory}" 2>/dev/null; then
    die "${EXIT_DATABASE}" "E504" \
      "Another recovery operation holds the backup-directory lock, or the lock path is unsafe"
  fi
  [[ -d "${lock_directory}" && ! -L "${lock_directory}" ]] \
    || die "${EXIT_DATABASE}" "E528" "Backup lock is not a real directory"
  BACKUP_LOCK_DIRECTORY="${lock_directory}"
}


release_backup_lock() {
  if [[ -n "${BACKUP_LOCK_DIRECTORY}" ]]; then
    if ! rmdir -- "${BACKUP_LOCK_DIRECTORY}" 2>/dev/null; then
      warn "Backup lock directory could not be removed: ${BACKUP_LOCK_DIRECTORY}"
    fi
    BACKUP_LOCK_DIRECTORY=""
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

  validate_supported_local_targets
  validate_running_target_configuration

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
    compose config --format json | python3 "${SAFETY_HELPER}" encryption-fingerprint
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


command_drill() {
  local backup=""
  local timeout_seconds=90
  local quarantine_hook="${THREATLENS_POST_RESTORE_HOOK:-${SCRIPT_DIR}/post_restore_quarantine.sh}"
  local acknowledge_encryption_mismatch=false
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
      --quarantine-hook)
        (($# >= 2)) || die "${EXIT_USAGE}" "E216" "--quarantine-hook requires a path"
        quarantine_hook="$2"
        shift 2
        ;;
      --acknowledge-encryption-key-mismatch)
        acknowledge_encryption_mismatch=true
        shift
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
  require_encryption_fingerprint_match "${acknowledge_encryption_mismatch}"
  validate_supported_local_targets
  validate_running_target_configuration
  quarantine_hook="$(_tlr_restore_resolve_hook "${quarantine_hook}")"
  stage_restore_inputs "${quarantine_hook}"
  quarantine_hook="${STAGED_QUARANTINE_HOOK}"
  local database_image application_image archive_checksum
  PINNED_DATABASE_IMAGE="$(resolve_database_image)"
  PINNED_APPLICATION_IMAGE="$(resolve_application_image)"
  database_image="${PINNED_DATABASE_IMAGE}"
  application_image="${PINNED_APPLICATION_IMAGE}"
  check_pg_restore_catalog "${VERIFIED_ARCHIVE}" "${database_image}"
  archive_checksum="${VERIFIED_ARCHIVE_SHA256}"
  local drill_status=0
  if tlr_run_isolated_drill \
    "${timeout_seconds}" "${database_image}" "${application_image}" "${quarantine_hook}"; then
    drill_status=0
  else
    drill_status=$?
  fi
  case "${drill_status}" in
    0) ;;
    11) die "${EXIT_DRILL}" "E601" "Unable to create the isolated drill network" ;;
    12) die "${EXIT_DRILL}" "E602" "Unable to create the drill data volume" ;;
    13) die "${EXIT_DRILL}" "E603" "Unable to start the isolated PostgreSQL drill target" ;;
    14) die "${EXIT_DRILL}" "E604" "Isolated PostgreSQL did not become ready before the timeout" ;;
    16) die "${EXIT_DRILL}" "E605" "Archive restore failed in the isolated target" ;;
    22) die "${EXIT_DRILL}" "E607" "Restored Alembic revision differs from the approved manifest" ;;
    40) die "${EXIT_DRILL}" "E615" "Packaged Alembic upgrade or API/schema smoke failed in isolation" ;;
    42) die "${EXIT_DRILL}" "E616" "Quarantine hook preflight rejected the upgraded isolated archive" ;;
    *) die "${EXIT_DRILL}" "E617" "Isolated recovery validation failed at stage ${drill_status}" ;;
  esac

  if ! cleanup_drill_resources; then
    die "${EXIT_DRILL}" "E614" "Drill passed, but one or more isolated Docker resources could not be removed"
  fi
  set_operation_metadata \
    --field tool_version=1 \
    --field "archive_sha256=${archive_checksum}" \
    --field "alembic_revision=${DRILL_UPGRADED_REVISION}" \
    --field "table_count=${DRILL_TABLE_COUNT}" \
    --field catalog_checked=true \
    --field redis_restored=false
  log "Isolated restore, current migration, API/schema smoke, and quarantine preflight passed"
  printf 'RESTORE_DRILL_STATUS=passed\nARCHIVE_SHA256=%s\nALEMBIC_REVISION=%s\nTABLE_COUNT=%s\n' \
    "${archive_checksum}" "${DRILL_UPGRADED_REVISION}" "${DRILL_TABLE_COUNT}"
}


# shellcheck source=scripts/recovery/recovery_drill_lib.sh
source "${SCRIPT_DIR}/recovery_drill_lib.sh"

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
      --journal-dir)
        (($# >= 2)) || die "${EXIT_USAGE}" "E229" "--journal-dir requires a path"
        RECOVERY_JOURNAL_ROOT="$2"
        shift 2
        ;;
      --help|-h)
        usage
        exit 0
        ;;
      backup|verify|drill|restore|reconcile)
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
  local argument
  for argument in "${COMMAND_ARGUMENTS[@]}"; do
    if [[ "${argument}" == "--help" ]]; then
      case "${COMMAND}" in
        backup) command_backup "${COMMAND_ARGUMENTS[@]}" ;;
        verify) command_verify "${COMMAND_ARGUMENTS[@]}" ;;
        drill) command_drill "${COMMAND_ARGUMENTS[@]}" ;;
        restore) tlr_restore_command "${COMMAND_ARGUMENTS[@]}" ;;
        reconcile) tlr_reconcile_command "${COMMAND_ARGUMENTS[@]}" ;;
      esac
      return 0
    fi
  done
  start_operation "${COMMAND}"
  case "${COMMAND}" in
    backup|verify|drill)
      acquire_recovery_operation_lock
      begin_operation_best_effort
      ;;
  esac
  case "${COMMAND}" in
    backup) command_backup "${COMMAND_ARGUMENTS[@]}" ;;
    verify) command_verify "${COMMAND_ARGUMENTS[@]}" ;;
    drill) command_drill "${COMMAND_ARGUMENTS[@]}" ;;
    restore) tlr_restore_command "${COMMAND_ARGUMENTS[@]}" ;;
    reconcile) tlr_reconcile_command "${COMMAND_ARGUMENTS[@]}" ;;
    *) die "${EXIT_USAGE}" "E226" "Unsupported command: ${COMMAND}" ;;
  esac
  finish_operation_best_effort succeeded
  archive_restore_journal_best_effort
}


main "$@"
