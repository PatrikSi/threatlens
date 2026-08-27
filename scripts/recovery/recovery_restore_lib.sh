#!/usr/bin/env bash

# Destructive restore implementation for threatlens-recovery.sh.
# Public surface: tlr_restore_command. Every other function is private to this file.
# Container-side shell fragments intentionally defer variable expansion.
# shellcheck disable=SC2016

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  printf 'ERROR [E227] recovery_restore_lib.sh must be sourced by threatlens-recovery.sh\n' >&2
  exit 2
fi


_tlr_restore_resolve_hook() {
  local hook="$1"
  [[ -n "${hook}" ]] \
    || die "${EXIT_REFUSED}" "E701" \
      "Destructive restore is disabled because no application quarantine/revocation hook is configured"
  [[ -f "${hook}" && -x "${hook}" && ! -L "${hook}" ]] \
    || die "${EXIT_REFUSED}" "E702" "Quarantine hook must be an executable regular file and not a symlink"
  local hook_directory hook_basename
  hook_directory="$(cd -- "$(dirname -- "${hook}")" && pwd -P)" \
    || die "${EXIT_REFUSED}" "E703" "Unable to resolve quarantine-hook directory"
  hook_basename="$(basename -- "${hook}")"
  printf '%s/%s\n' "${hook_directory}" "${hook_basename}"
}


_tlr_restore_run_hook() {
  local hook="$1"
  local phase="$2"
  local compose_files_text
  printf -v compose_files_text '%s\n' "${COMPOSE_FILES[@]}"
  THREATLENS_RECOVERY_PHASE="${phase}" \
  THREATLENS_RECOVERY_COMPOSE_FILES="${compose_files_text%$'\n'}" \
  THREATLENS_RECOVERY_ENV_FILE="${ENV_FILE}" \
  THREATLENS_RECOVERY_PROJECT_NAME="${COMPOSE_PROJECT}" \
  THREATLENS_RECOVERY_MANIFEST="${VERIFIED_MANIFEST}" \
  THREATLENS_RECOVERY_ARCHIVE_SHA256="${VERIFIED_ARCHIVE_SHA256}" \
    "${hook}" "${phase}"
}


_tlr_restore_stop_application_services() {
  local -a services=(api worker worker-ai worker-maintenance worker-notifications beat web)
  log "Stopping API, workers, scheduler, and web before database replacement"
  if ! compose stop --timeout 60 "${services[@]}"; then
    die "${EXIT_RESTORE}" "E801" "Application services could not all be stopped; database was not replaced"
  fi
  local running_services service
  running_services="$(compose ps --services --status running)" \
    || die "${EXIT_RESTORE}" "E802" "Unable to verify application service shutdown"
  for service in "${services[@]}"; do
    if grep -Fxq -- "${service}" <<<"${running_services}"; then
      die "${EXIT_RESTORE}" "E803" "Application service remained running after stop: ${service}"
    fi
  done
}


_tlr_restore_create_clean_database() {
  local rollback_database="$1"
  compose exec -T --env "THREATLENS_ROLLBACK_DB=${rollback_database}" db sh -ceu '
    psql --no-psqlrc --set=ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
      --set=db_name="$POSTGRES_DB" --set=owner_name="$POSTGRES_USER" \
      --set=rollback_db="$THREATLENS_ROLLBACK_DB" <<'"'"'SQL'"'"'
SELECT pg_terminate_backend(pid)
FROM pg_catalog.pg_stat_activity
WHERE datname = :'"'"'db_name'"'"' AND pid <> pg_backend_pid();
SELECT format('"'"'ALTER DATABASE %I RENAME TO %I'"'"', :'"'"'db_name'"'"', :'"'"'rollback_db'"'"') \gexec
SELECT format('"'"'CREATE DATABASE %I WITH TEMPLATE template0 OWNER %I'"'"', :'"'"'db_name'"'"', :'"'"'owner_name'"'"') \gexec
SQL
  '
}


_tlr_restore_database_exists() {
  local database_name="$1"
  local exists
  exists="$(compose exec -T --env "THREATLENS_DATABASE_NAME=${database_name}" db sh -ceu '
    exec psql --no-psqlrc --set=ON_ERROR_STOP=1 --tuples-only --no-align \
      --username "$POSTGRES_USER" --dbname postgres \
      --set=database_name="$THREATLENS_DATABASE_NAME" --command \
      "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_database WHERE datname = :'"'"'database_name'"'"');"
  ' | tr -d '[:space:]')" || return 1
  [[ "${exists}" == "t" ]]
}


_tlr_restore_rollback_database() {
  local rollback_database="$1"
  if ! compose exec -T --env "THREATLENS_ROLLBACK_DB=${rollback_database}" db sh -ceu '
    psql --no-psqlrc --set=ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
      --set=db_name="$POSTGRES_DB" --set=rollback_db="$THREATLENS_ROLLBACK_DB" <<'"'"'SQL'"'"'
SELECT pg_terminate_backend(pid)
FROM pg_catalog.pg_stat_activity
WHERE datname = :'"'"'db_name'"'"' AND pid <> pg_backend_pid();
SELECT format('"'"'DROP DATABASE IF EXISTS %I'"'"', :'"'"'db_name'"'"') \gexec
SELECT format('"'"'ALTER DATABASE %I RENAME TO %I'"'"', :'"'"'rollback_db'"'"', :'"'"'db_name'"'"') \gexec
SQL
  '; then
    return 1
  fi
  # These globals are owned by the sourcing recovery dispatcher.
  # shellcheck disable=SC2034
  RESTORE_REPLACEMENT_ACTIVE=false
  # shellcheck disable=SC2034
  RESTORE_ROLLBACK_DATABASE=""
  return 0
}


_tlr_restore_drop_rollback_database() {
  local rollback_database="$1"
  if ! compose exec -T --env "THREATLENS_ROLLBACK_DB=${rollback_database}" db sh -ceu '
    psql --no-psqlrc --set=ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
      --set=rollback_db="$THREATLENS_ROLLBACK_DB" <<'"'"'SQL'"'"'
SELECT pg_terminate_backend(pid)
FROM pg_catalog.pg_stat_activity
WHERE datname = :'"'"'rollback_db'"'"' AND pid <> pg_backend_pid();
SELECT format('"'"'DROP DATABASE %I'"'"', :'"'"'rollback_db'"'"') \gexec
SQL
  '; then
    return 1
  fi
  # These globals are owned by the sourcing recovery dispatcher.
  # shellcheck disable=SC2034
  RESTORE_REPLACEMENT_ACTIVE=false
  # shellcheck disable=SC2034
  RESTORE_ROLLBACK_DATABASE=""
  return 0
}


_tlr_restore_archive() {
  local archive="$1"
  compose exec -T db sh -ceu '
    exec pg_restore --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
      --exit-on-error --single-transaction --no-owner --no-privileges
  ' <"${archive}"
}


_tlr_restore_clear_redis() {
  compose exec -T redis sh -ceu '
    REDISCLI_AUTH="$REDIS_PASSWORD" exec redis-cli --no-auth-warning FLUSHDB
  ' >/dev/null
}


_tlr_restore_record_failure_before_rollback() {
  local error_code="$1"
  finish_operation_best_effort failed "${error_code}"
  OPERATION_LEDGER_ALLOWED=false
}


_tlr_restore_emergency_rollback() {
  if [[ "${RESTORE_REPLACEMENT_ACTIVE:-false}" != true \
    || -z "${RESTORE_ROLLBACK_DATABASE:-}" ]]; then
    return 0
  fi
  warn "Recovery was interrupted after database replacement; attempting emergency rollback"
  if _tlr_restore_rollback_database "${RESTORE_ROLLBACK_DATABASE}"; then
    # Never write restore history into the original, non-quarantined database.
    # shellcheck disable=SC2034
    OPERATION_LEDGER_ALLOWED=false
    warn "Emergency rollback restored the pre-restore database; application services remain stopped"
    return 0
  fi
  warn "CRITICAL: emergency database rollback failed; keep all application services stopped and escalate"
  return 1
}


tlr_restore_command() {
  local backup=""
  local confirmation=""
  local acknowledge_data_loss=false
  local quarantine_hook="${THREATLENS_POST_RESTORE_HOOK:-${SCRIPT_DIR}/post_restore_quarantine.sh}"
  local safety_backup_directory="${THREATLENS_BACKUP_DIR:-${REPOSITORY_ROOT}/backups}/pre-restore"
  local allow_version_mismatch=false
  while (($#)); do
    case "$1" in
      --backup)
        (($# >= 2)) || die "${EXIT_USAGE}" "E214" "--backup requires a path"
        backup="$2"
        shift 2
        ;;
      --confirm)
        (($# >= 2)) || die "${EXIT_USAGE}" "E215" "--confirm requires the exact confirmation text"
        confirmation="$2"
        shift 2
        ;;
      --acknowledge-data-loss)
        acknowledge_data_loss=true
        shift
        ;;
      --quarantine-hook)
        (($# >= 2)) || die "${EXIT_USAGE}" "E216" "--quarantine-hook requires a path"
        quarantine_hook="$2"
        shift 2
        ;;
      --safety-backup-dir)
        (($# >= 2)) || die "${EXIT_USAGE}" "E217" "--safety-backup-dir requires a path"
        safety_backup_directory="$2"
        shift 2
        ;;
      --allow-app-version-mismatch)
        allow_version_mismatch=true
        shift
        ;;
      --help)
        usage
        exit 0
        ;;
      *) die "${EXIT_USAGE}" "E218" "Unknown restore option: $1" ;;
    esac
  done

  [[ -n "${backup}" ]] || die "${EXIT_USAGE}" "E219" "restore requires --backup"
  [[ "${confirmation}" == "${RESTORE_CONFIRMATION}" ]] \
    || die "${EXIT_REFUSED}" "E704" \
      "Destructive restore refused; --confirm must exactly equal '${RESTORE_CONFIRMATION}'"
  [[ "${acknowledge_data_loss}" == true ]] \
    || die "${EXIT_REFUSED}" "E705" \
      "Destructive restore refused; --acknowledge-data-loss is also required"
  quarantine_hook="$(_tlr_restore_resolve_hook "${quarantine_hook}")"

  local expected_version=""
  if [[ "${allow_version_mismatch}" != true ]]; then
    expected_version="$(resolve_app_version)"
  fi
  prepare_verified_archive "${backup}" "${expected_version}"
  local database_image
  database_image="$(resolve_database_image)"
  check_pg_restore_catalog "${VERIFIED_ARCHIVE}" "${database_image}"

  require_compose_services db redis api worker worker-ai worker-maintenance worker-notifications beat web
  require_running_service db
  require_running_service redis
  require_database_ready
  if ! _tlr_restore_run_hook "${quarantine_hook}" preflight; then
    die "${EXIT_REFUSED}" "E706" \
      "Quarantine hook preflight failed; no service was stopped and no database was replaced"
  fi

  log "Creating mandatory fresh safety backup before destructive restore"
  local safety_backup
  safety_backup="$(perform_backup "${safety_backup_directory}")" \
    || die "${EXIT_RESTORE}" "E804" "Fresh safety backup failed; destructive restore was not started"

  _tlr_restore_stop_application_services
  require_running_service db
  local rollback_database random_suffix
  random_suffix="$(python3 -c 'import secrets; print(secrets.token_hex(3))')"
  rollback_database="tl_pre_restore_$(date -u +'%Y%m%dT%H%M%SZ')_${random_suffix}"

  log "Renaming the current database for rollback and creating a clean restore target"
  if ! _tlr_restore_create_clean_database "${rollback_database}"; then
    if _tlr_restore_database_exists "${rollback_database}"; then
      warn "Clean-target creation stopped after the database rename; attempting automatic rollback"
      if _tlr_restore_rollback_database "${rollback_database}"; then
        die "${EXIT_RESTORE}" "E805" \
          "Could not create a clean restore target and the prior database was restored; application services remain stopped"
      fi
      die "${EXIT_RESTORE}" "E813" \
        "CRITICAL: clean-target creation and automatic database rollback both failed; keep all services stopped and escalate"
    fi
    die "${EXIT_RESTORE}" "E805" \
      "Could not create a clean restore target; the original database was not renamed and application services remain stopped"
  fi
  # These globals let the EXIT trap recover from signals or unhandled failures.
  # shellcheck disable=SC2034
  RESTORE_ROLLBACK_DATABASE="${rollback_database}"
  # shellcheck disable=SC2034
  RESTORE_REPLACEMENT_ACTIVE=true

  if ! _tlr_restore_archive "${VERIFIED_ARCHIVE}"; then
    warn "Archive restore failed; attempting to put the pre-restore database back"
    if _tlr_restore_rollback_database "${rollback_database}"; then
      die "${EXIT_RESTORE}" "E806" \
        "Archive restore failed and the prior database was restored; application services remain stopped"
    fi
    die "${EXIT_RESTORE}" "E807" \
      "CRITICAL: archive restore and automatic database rollback both failed; keep all services stopped and escalate"
  fi

  if ! _tlr_restore_run_hook "${quarantine_hook}" apply \
    || ! _tlr_restore_run_hook "${quarantine_hook}" verify; then
    warn "Post-restore quarantine/revocation did not verify; attempting database rollback"
    if _tlr_restore_rollback_database "${rollback_database}"; then
      die "${EXIT_RESTORE}" "E808" \
        "Restore was rolled back because quarantine/revocation did not verify; application services remain stopped"
    fi
    die "${EXIT_RESTORE}" "E809" \
      "CRITICAL: quarantine verification and automatic database rollback both failed; keep all services stopped and escalate"
  fi

  set_operation_metadata \
    --field tool_version=1 \
    --field "archive_sha256=${VERIFIED_ARCHIVE_SHA256}" \
    --field "app_version=${VERIFIED_APP_VERSION}" \
    --field "alembic_revision=${VERIFIED_ALEMBIC_REVISION}" \
    --field outbound_quarantined=true \
    --field redis_restored=false
  # This state is owned and consumed by the sourcing recovery dispatcher.
  # shellcheck disable=SC2034
  OPERATION_LEDGER_ALLOWED=true

  if ! compose up --detach redis >/dev/null; then
    warn "Redis could not be made available; attempting database rollback"
    _tlr_restore_record_failure_before_rollback E810
    if _tlr_restore_rollback_database "${rollback_database}"; then
      die "${EXIT_RESTORE}" "E810" \
        "Restore was rolled back because Redis could not be made available for clearing; application services remain stopped"
    fi
    die "${EXIT_RESTORE}" "E814" \
      "CRITICAL: Redis startup and automatic database rollback both failed; keep all services stopped and escalate"
  fi
  if ! _tlr_restore_clear_redis; then
    warn "Redis could not be cleared; attempting database rollback"
    _tlr_restore_record_failure_before_rollback E811
    if _tlr_restore_rollback_database "${rollback_database}"; then
      die "${EXIT_RESTORE}" "E811" \
        "Restore was rolled back because Redis could not be cleared; application services remain stopped"
    fi
    die "${EXIT_RESTORE}" "E815" \
      "CRITICAL: Redis clearing and automatic database rollback both failed; keep all services stopped and escalate"
  fi
  if ! _tlr_restore_drop_rollback_database "${rollback_database}"; then
    die "${EXIT_RESTORE}" "E812" \
      "Restore and quarantine succeeded, but the temporary rollback database could not be removed; application services remain stopped"
  fi

  log "Destructive restore completed; Redis was cleared and application services remain stopped"
  printf 'RESTORE_STATUS=completed_quarantined\nSAFETY_BACKUP=%s\n' "${safety_backup}"
  printf '%s\n' \
    "Review the quarantine evidence, then start only the services you intend to resume." >&2
}
