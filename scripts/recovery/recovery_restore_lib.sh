#!/usr/bin/env bash

# Destructive restore implementation for threatlens-recovery.sh.
# Public surface: tlr_restore_command. Every other function is private.
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
  THREATLENS_RECOVERY_MANIFEST_HELPER="${STAGED_MANIFEST_HELPER}" \
  THREATLENS_RECOVERY_ARCHIVE_SHA256="${VERIFIED_ARCHIVE_SHA256}" \
  THREATLENS_RECOVERY_DATABASE_USER="${RESTORE_RECOVERY_ROLE}" \
  THREATLENS_RECOVERY_DATABASE_NAME="${SUPPORTED_DATABASE_NAME}" \
    "${hook}" "${phase}"
}


_tlr_restore_stop_application_services() {
  local -a services=()
  local service
  while IFS= read -r service; do
    [[ -n "${service}" ]] && services+=("${service}")
  done < <(target_backend_services)
  local available_services
  available_services="$(compose config --services)" \
    || die "${EXIT_RESTORE}" "E802" "Unable to inspect configured application services"
  if grep -Fxq web <<<"${available_services}"; then
    services+=(web)
  fi
  ((${#services[@]} > 0)) \
    || die "${EXIT_RESTORE}" "E803" "No validated backend application services were discovered"
  log "Stopping API, workers, scheduler, and web before database replacement"
  if ! compose stop --timeout 60 "${services[@]}"; then
    die "${EXIT_RESTORE}" "E801" "Application services could not all be stopped; database was not replaced"
  fi
  local running_services
  running_services="$(compose ps --services --status running)" \
    || die "${EXIT_RESTORE}" "E802" "Unable to verify application service shutdown"
  for service in "${services[@]}"; do
    if grep -Fxq -- "${service}" <<<"${running_services}"; then
      die "${EXIT_RESTORE}" "E803" "Application service remained running after stop: ${service}"
    fi
  done
}


_tlr_restore_database_state() {
  local database_name="$1"
  local exists
  if ! exists="$(
    compose exec -T \
      --env "THREATLENS_DATABASE_NAME=${database_name}" \
      --env "THREATLENS_RECOVERY_ROLE=${RESTORE_RECOVERY_ROLE}" db sh -ceu '
      selected_user=""
      for candidate in "${THREATLENS_RECOVERY_ROLE:-}" "$POSTGRES_USER"; do
        [ -n "$candidate" ] || continue
        if psql --no-psqlrc --set=ON_ERROR_STOP=1 --quiet --username "$candidate" \
          --dbname postgres --command "SELECT 1" >/dev/null 2>&1; then
          selected_user="$candidate"
          break
        fi
      done
      [ -n "$selected_user" ] || exit 91
      exec psql --no-psqlrc --set=ON_ERROR_STOP=1 --tuples-only --no-align \
        --username "$selected_user" --dbname postgres \
        --set=db_name="$THREATLENS_DATABASE_NAME"
    ' <<'SQL' | tr -d '[:space:]'
SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_database WHERE datname = :'db_name');
SQL
  )"; then
    return 2
  fi
  case "${exists}" in
    t) printf 'present\n' ;;
    f) printf 'missing\n' ;;
    *) return 2 ;;
  esac
}


_tlr_restore_database_oid() {
  local database_name="$1"
  local oid
  oid="$(
    compose exec -T \
      --env "THREATLENS_DATABASE_NAME=${database_name}" \
      --env "THREATLENS_RECOVERY_ROLE=${RESTORE_RECOVERY_ROLE}" db sh -ceu '
      selected_user=""
      for candidate in "${THREATLENS_RECOVERY_ROLE:-}" "$POSTGRES_USER"; do
        [ -n "$candidate" ] || continue
        if psql --no-psqlrc --set=ON_ERROR_STOP=1 --quiet --username "$candidate" \
          --dbname postgres --command "SELECT 1" >/dev/null 2>&1; then
          selected_user="$candidate"
          break
        fi
      done
      [ -n "$selected_user" ] || exit 91
      exec psql --no-psqlrc --set=ON_ERROR_STOP=1 --tuples-only --no-align \
        --username "$selected_user" --dbname postgres \
        --set=db_name="$THREATLENS_DATABASE_NAME"
    ' <<'SQL' | tr -d '[:space:]'
SELECT oid::text FROM pg_catalog.pg_database WHERE datname = :'db_name';
SQL
  )" || return 1
  [[ "${oid}" =~ ^[1-9][0-9]*$ ]] || return 1
  printf '%s\n' "${oid}"
}


_tlr_restore_capture_original_access_state() {
  local state
  if ! state="$(
    compose exec -T db sh -ceu '
      exec psql --no-psqlrc --set=ON_ERROR_STOP=1 --tuples-only --no-align \
        --username "$POSTGRES_USER" --dbname postgres \
        --set=db_name="$POSTGRES_DB" --set=app_role="$POSTGRES_USER"
    ' <<'SQL' | tr -d '[:space:]'
SELECT concat_ws(chr(124), role.rolcanlogin::text, database.datallowconn::text, database.oid::text)
FROM pg_catalog.pg_roles AS role
JOIN pg_catalog.pg_database AS database ON database.datname = :'db_name'
WHERE role.rolname = :'app_role';
SQL
  )"; then
    return 1
  fi
  case "${state}" in
    true\|true\|*|t\|t\|*)
      RESTORE_ORIGINAL_ROLE_CAN_LOGIN=true
      RESTORE_ORIGINAL_DATABASE_ALLOW_CONNECTIONS=true
      RESTORE_ORIGINAL_DATABASE_OID="${state##*|}"
      [[ "${RESTORE_ORIGINAL_DATABASE_OID}" =~ ^[1-9][0-9]*$ ]] || return 1
      ;;
    *)
      warn "Recovery requires the application role and database to accept connections before fencing"
      return 1
      ;;
  esac
}


_tlr_restore_journal_init() {
  python3 "${JOURNAL_HELPER}" init \
    --root "${RECOVERY_JOURNAL_ROOT}" \
    --operation-id "${OPERATION_ID}" \
    --started-at "${OPERATION_STARTED_AT}" \
    --project "${COMPOSE_PROJECT}" \
    --database "${SUPPORTED_DATABASE_NAME}" \
    --archive-sha256 "${VERIFIED_ARCHIVE_SHA256}" \
    --target-config-sha256 "${TARGET_CONFIG_SHA256}" \
    --target-deployment-identity "${TARGET_DEPLOYMENT_IDENTITY}" \
    --rollback-database "${RESTORE_ROLLBACK_DATABASE}" \
    --recovery-role "${RESTORE_RECOVERY_ROLE}" \
    --original-database-oid "${RESTORE_ORIGINAL_DATABASE_OID}" \
    --original-role-can-login "${RESTORE_ORIGINAL_ROLE_CAN_LOGIN}" \
    --original-database-allow-connections "${RESTORE_ORIGINAL_DATABASE_ALLOW_CONNECTIONS}" \
    >/dev/null || return 1
  RESTORE_JOURNAL_ACTIVE=true
}


_tlr_restore_set_phase() {
  local phase="$1"
  shift
  if [[ "${RESTORE_JOURNAL_ACTIVE}" == true ]]; then
    python3 "${JOURNAL_HELPER}" update --root "${RECOVERY_JOURNAL_ROOT}" \
      --updated-at "$(timestamp)" --phase "${phase}" "$@" >/dev/null || return 1
  fi
  RESTORE_PHASE="${phase}"
}


_tlr_restore_journal_terminal() {
  local status="$1"
  local outcome="$2"
  local error_code="$3"
  [[ "${RESTORE_JOURNAL_ACTIVE:-false}" == true ]] || return 0
  local -a args=(
    update --root "${RECOVERY_JOURNAL_ROOT}" --updated-at "$(timestamp)"
    --phase completed --status "${status}" --outcome "${outcome}"
  )
  [[ -z "${error_code}" ]] || args+=(--error-code "${error_code}")
  python3 "${JOURNAL_HELPER}" "${args[@]}" >/dev/null || {
    warn "CRITICAL: terminal recovery state could not be written to the durable host journal"
    return 1
  }
  RESTORE_PHASE="completed"
}


_tlr_restore_load_journal() {
  local -a fields=()
  mapfile -t fields < <(python3 "${JOURNAL_HELPER}" inspect --root "${RECOVERY_JOURNAL_ROOT}") \
    || return 1
  ((${#fields[@]} == 17)) || return 1
  OPERATION_ID="${fields[0]}"
  OPERATION_STARTED_AT="${fields[1]}"
  [[ "${fields[2]}" == "${COMPOSE_PROJECT}" ]] || return 2
  SUPPORTED_DATABASE_NAME="${fields[3]}"
  VERIFIED_ARCHIVE_SHA256="${fields[4]}"
  RESTORE_JOURNAL_TARGET_CONFIG_SHA256="${fields[5]}"
  TARGET_DEPLOYMENT_IDENTITY="${fields[6]}"
  RESTORE_PHASE="${fields[7]}"
  RESTORE_JOURNAL_STATUS="${fields[8]}"
  RESTORE_JOURNAL_OUTCOME="${fields[9]}"
  RESTORE_ROLLBACK_DATABASE="${fields[10]}"
  RESTORE_RECOVERY_ROLE="${fields[11]}"
  RESTORE_ORIGINAL_DATABASE_OID="${fields[12]}"
  RESTORE_REPLACEMENT_DATABASE_OID="${fields[13]}"
  RESTORE_ORIGINAL_ROLE_CAN_LOGIN="${fields[14]}"
  RESTORE_ORIGINAL_DATABASE_ALLOW_CONNECTIONS="${fields[15]}"
  RESTORE_JOURNAL_ERROR_CODE="${fields[16]}"
  RESTORE_JOURNAL_ACTIVE=true
  OPERATION_TYPE="restore"
  OPERATION_LEDGER_ALLOWED=false
  OPERATION_FINISHED=false
  OPERATION_EVIDENCE_RECORDED=false
}


_tlr_restore_ingest_terminal_journal() {
  set_operation_metadata \
    --field tool_version=1 \
    --field "archive_sha256=${VERIFIED_ARCHIVE_SHA256}" \
    --field redis_restored=false \
    --field reconciled_after_interruption=true \
    --field "journal_outcome=${RESTORE_JOURNAL_OUTCOME}"
  OPERATION_LEDGER_ALLOWED=true
  if [[ "${RESTORE_JOURNAL_STATUS}" == "succeeded" ]]; then
    finish_operation_best_effort succeeded
  elif [[ "${RESTORE_JOURNAL_STATUS}" == "failed" ]]; then
    finish_operation_best_effort failed "${RESTORE_JOURNAL_ERROR_CODE:-E_INTERRUPTED_RECONCILED}"
  else
    return 1
  fi
  [[ "${OPERATION_EVIDENCE_RECORDED}" == true ]] || return 2
  archive_restore_journal_best_effort
}


_tlr_restore_arm_rollback() {
  RESTORE_ROLLBACK_DATABASE="$1"
  RESTORE_RECOVERY_ROLE="$2"
  RESTORE_RECOVERY_PASSWORD="$3"
  RESTORE_PHASE="prepared_before_fence"
  RESTORE_REPLACEMENT_ACTIVE=true
}


_tlr_restore_create_clean_database() {
  (
    export THREATLENS_RECOVERY_PASSWORD="${RESTORE_RECOVERY_PASSWORD}"
    compose exec -T \
      --env "THREATLENS_ROLLBACK_DB=${RESTORE_ROLLBACK_DATABASE}" \
      --env "THREATLENS_RECOVERY_ROLE=${RESTORE_RECOVERY_ROLE}" \
      --env THREATLENS_RECOVERY_PASSWORD \
      db sh -ceu '
      exec psql --no-psqlrc --set=ON_ERROR_STOP=1 --username "$POSTGRES_USER" \
        --dbname postgres --set=db_name="$POSTGRES_DB" \
        --set=app_role="$POSTGRES_USER" \
        --set=rollback_db="$THREATLENS_ROLLBACK_DB" \
        --set=recovery_role="$THREATLENS_RECOVERY_ROLE" \
        --set=recovery_password="$THREATLENS_RECOVERY_PASSWORD"
      ' <<'SQL'
SELECT format(
  'CREATE ROLE %I WITH LOGIN SUPERUSER CONNECTION LIMIT 4 PASSWORD %L',
  :'recovery_role', :'recovery_password'
) \gexec
SELECT format('ALTER ROLE %I NOLOGIN', :'app_role') \gexec
SELECT format('ALTER DATABASE %I WITH ALLOW_CONNECTIONS false', :'db_name') \gexec
SELECT pg_terminate_backend(pid)
FROM pg_catalog.pg_stat_activity
WHERE datname = :'db_name' AND pid <> pg_backend_pid();
SELECT format('ALTER DATABASE %I RENAME TO %I', :'db_name', :'rollback_db') \gexec
SELECT format(
  'CREATE DATABASE %I WITH TEMPLATE template0 OWNER %I ENCODING %L LC_COLLATE %L LC_CTYPE %L TABLESPACE %I CONNECTION LIMIT %s ALLOW_CONNECTIONS false%s',
  :'db_name',
  :'app_role',
  pg_encoding_to_char(database.encoding),
  database.datcollate,
  database.datctype,
  tablespace.spcname,
  database.datconnlimit,
  CASE database.datlocprovider
    WHEN 'i' THEN format(
      ' LOCALE_PROVIDER icu ICU_LOCALE %L%s',
      database.daticulocale,
      CASE WHEN database.daticurules IS NULL THEN ''
           ELSE format(' ICU_RULES %L', database.daticurules) END
    )
    ELSE ' LOCALE_PROVIDER libc'
  END
)
FROM pg_catalog.pg_database AS database
JOIN pg_catalog.pg_tablespace AS tablespace ON tablespace.oid = database.dattablespace
WHERE database.datname = :'rollback_db' \gexec
SELECT format('REVOKE CONNECT ON DATABASE %I FROM PUBLIC', :'db_name') \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', :'db_name', :'recovery_role') \gexec
SELECT format('ALTER DATABASE %I WITH ALLOW_CONNECTIONS true', :'db_name') \gexec
SQL
  )
}


_tlr_restore_rollback_database() {
  local rollback_state target_state rollback_status target_status rollback_oid="" target_oid=""
  if rollback_state="$(_tlr_restore_database_state "${RESTORE_ROLLBACK_DATABASE}")"; then
    rollback_status=0
  else
    rollback_status=$?
  fi
  if target_state="$(_tlr_restore_database_state "${SUPPORTED_DATABASE_NAME}")"; then
    target_status=0
  else
    target_status=$?
  fi
  if ((rollback_status != 0 || target_status != 0)); then
    warn "Database existence probe failed during rollback reconciliation"
    return 2
  fi
  [[ "${rollback_state}" == "present" || "${target_state}" == "present" ]] || return 3

  if [[ "${rollback_state}" == "present" ]]; then
    rollback_oid="$(_tlr_restore_database_oid "${RESTORE_ROLLBACK_DATABASE}")" || return 4
    [[ "${rollback_oid}" == "${RESTORE_ORIGINAL_DATABASE_OID}" ]] || {
      warn "Rollback database OID does not match the original database identity"
      return 5
    }
  fi
  if [[ "${target_state}" == "present" ]]; then
    target_oid="$(_tlr_restore_database_oid "${SUPPORTED_DATABASE_NAME}")" || return 6
  fi

  if [[ "${rollback_state}" == "missing" ]]; then
    if [[ "${target_oid}" == "${RESTORE_ORIGINAL_DATABASE_OID}" ]]; then
      log "The original database OID is already present at the application name; finishing rollback cleanup"
    elif [[ -n "${RESTORE_REPLACEMENT_DATABASE_OID}" \
      && "${target_oid}" == "${RESTORE_REPLACEMENT_DATABASE_OID}" \
      && "${RESTORE_PHASE}" =~ ^(forward_commit_requested|rollback_database_removed|completed)$ ]]; then
      _tlr_restore_verify_forward_commit || return 7
      RESTORE_PHASE="forward_committed"
      RESTORE_REPLACEMENT_ACTIVE=false
      RESTORE_FORWARD_COMMITTED=true
      return 0
    else
      warn "Target database identity cannot be classified as the original or committed replacement"
      return 8
    fi
  fi

  compose exec -T \
    --env "THREATLENS_ROLLBACK_DB=${RESTORE_ROLLBACK_DATABASE}" \
    --env "THREATLENS_RECOVERY_ROLE=${RESTORE_RECOVERY_ROLE}" \
    --env "THREATLENS_TARGET_STATE=${target_state}" \
    --env "THREATLENS_ROLLBACK_STATE=${rollback_state}" \
    --env "THREATLENS_ORIGINAL_ROLE_CAN_LOGIN=${RESTORE_ORIGINAL_ROLE_CAN_LOGIN}" \
    --env "THREATLENS_ORIGINAL_DATABASE_ALLOW_CONNECTIONS=${RESTORE_ORIGINAL_DATABASE_ALLOW_CONNECTIONS}" \
    db sh -ceu '
      selected_user=""
      for candidate in "$THREATLENS_RECOVERY_ROLE" "$POSTGRES_USER"; do
        [ -n "$candidate" ] || continue
        if psql --no-psqlrc --set=ON_ERROR_STOP=1 --quiet \
          --username "$candidate" --dbname postgres --command "SELECT 1" \
          >/dev/null 2>&1; then
          selected_user="$candidate"
          break
        fi
      done
      [ -n "$selected_user" ] || exit 91
      exec psql --no-psqlrc --set=ON_ERROR_STOP=1 --username "$selected_user" \
        --dbname postgres --set=db_name="$POSTGRES_DB" \
        --set=app_role="$POSTGRES_USER" \
        --set=rollback_db="$THREATLENS_ROLLBACK_DB" \
        --set=recovery_role="$THREATLENS_RECOVERY_ROLE" \
        --set=target_state="$THREATLENS_TARGET_STATE" \
        --set=rollback_state="$THREATLENS_ROLLBACK_STATE" \
        --set=original_role_can_login="$THREATLENS_ORIGINAL_ROLE_CAN_LOGIN" \
        --set=original_database_allow_connections="$THREATLENS_ORIGINAL_DATABASE_ALLOW_CONNECTIONS"
    ' <<'SQL' || return 1
SELECT format('ALTER DATABASE %I WITH ALLOW_CONNECTIONS false', :'db_name')
WHERE :'target_state' = 'present' \gexec
SELECT pg_terminate_backend(pid)
FROM pg_catalog.pg_stat_activity
WHERE datname = :'db_name' AND pid <> pg_backend_pid();
SELECT format('DROP DATABASE %I', :'db_name')
WHERE :'rollback_state' = 'present' AND :'target_state' = 'present' \gexec
SELECT format('ALTER DATABASE %I RENAME TO %I', :'rollback_db', :'db_name')
WHERE :'rollback_state' = 'present' \gexec
SELECT format(
  'ALTER DATABASE %I WITH ALLOW_CONNECTIONS %s',
  :'db_name', :'original_database_allow_connections'
) \gexec
SELECT format(
  'ALTER ROLE %I %s',
  :'app_role', CASE WHEN :'original_role_can_login' = 'true' THEN 'LOGIN' ELSE 'NOLOGIN' END
) \gexec
SQL

  compose exec -T \
    --env "THREATLENS_RECOVERY_ROLE=${RESTORE_RECOVERY_ROLE}" \
    db sh -ceu '
      exec psql --no-psqlrc --set=ON_ERROR_STOP=1 --username "$POSTGRES_USER" \
        --dbname postgres --set=recovery_role="$THREATLENS_RECOVERY_ROLE"
    ' <<'SQL' || return 1
SELECT format('DROP ROLE IF EXISTS %I', :'recovery_role') \gexec
SQL

  RESTORE_PHASE="rolled_back"
  RESTORE_REPLACEMENT_ACTIVE=false
  # Consumed by the sourcing dispatcher's fail-closed exit path.
  # shellcheck disable=SC2034
  RESTORE_RECONCILED_ROLLBACK=true
  RESTORE_RECOVERY_PASSWORD=""
  return 0
}


_tlr_restore_verify_forward_commit() {
  local state
  state="$(
    compose exec -T \
    --env "THREATLENS_ROLLBACK_DB=${RESTORE_ROLLBACK_DATABASE}" \
    --env "THREATLENS_RECOVERY_ROLE=${RESTORE_RECOVERY_ROLE}" \
    --env "THREATLENS_RESTORE_CHECKSUM=${VERIFIED_ARCHIVE_SHA256}" \
    --env "THREATLENS_EXPECTED_OID=${RESTORE_REPLACEMENT_DATABASE_OID}" \
    db sh -ceu '
      exec psql --no-psqlrc --set=ON_ERROR_STOP=1 --tuples-only --no-align \
        --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
        --set=rollback_db="$THREATLENS_ROLLBACK_DB" \
        --set=recovery_role="$THREATLENS_RECOVERY_ROLE" \
        --set=restore_checksum="$THREATLENS_RESTORE_CHECKSUM" \
        --set=expected_oid="$THREATLENS_EXPECTED_OID"
    ' <<'SQL' | tr -d '[:space:]'
SELECT concat_ws('|',
  CASE WHEN database.oid::text = :'expected_oid' THEN '1' ELSE '0' END,
  CASE WHEN database.datallowconn THEN '1' ELSE '0' END,
  CASE WHEN database.datdba = role.oid THEN '1' ELSE '0' END,
  CASE WHEN role.rolcanlogin THEN '1' ELSE '0' END,
  CASE WHEN NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_database WHERE datname = :'rollback_db'
  ) THEN '1' ELSE '0' END,
  CASE WHEN NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = :'recovery_role'
  ) THEN '1' ELSE '0' END,
  CASE WHEN EXISTS (
    SELECT 1 FROM audit_logs
    WHERE action = 'system.restore.quarantine'
      AND resource_type = 'postgresql_backup'
      AND resource_id = :'restore_checksum'
      AND success IS TRUE
  ) THEN '1' ELSE '0' END)
FROM pg_catalog.pg_database AS database
JOIN pg_catalog.pg_roles AS role ON role.rolname = current_user
WHERE database.datname = current_database();
SQL
  )" || return 1
  [[ "${state}" == "1|1|1|1|1|1|1" ]]
}


_tlr_restore_emergency_rollback() {
  if [[ "${RESTORE_REPLACEMENT_ACTIVE:-false}" != true \
    || -z "${RESTORE_ROLLBACK_DATABASE:-}" ]]; then
    return 0
  fi
  warn "Recovery stopped in phase '${RESTORE_PHASE:-unknown}'; reconciling the original database before recording failure"
  local attempt
  for attempt in 1 2 3; do
    if _tlr_restore_rollback_database; then
      # Consumed by the sourcing dispatcher's operation ledger.
      # shellcheck disable=SC2034
      OPERATION_LEDGER_ALLOWED=true
      if [[ "${RESTORE_FORWARD_COMMITTED:-false}" == true ]]; then
        warn "Forward-commit reconciliation proved the restored database and quarantine marker; services remain stopped"
      else
        warn "Rollback reconciliation restored the original database identity; application services remain stopped"
      fi
      return 0
    fi
    if ((attempt < 3)); then
      warn "Rollback reconciliation attempt ${attempt} failed; retrying after a bounded delay"
      sleep 1
    fi
  done
  # shellcheck disable=SC2034
  OPERATION_LEDGER_ALLOWED=false
  if [[ "${RESTORE_JOURNAL_ACTIVE:-false}" == true ]]; then
    python3 "${JOURNAL_HELPER}" update --root "${RECOVERY_JOURNAL_ROOT}" \
      --updated-at "$(timestamp)" --status unknown --outcome unknown >/dev/null || true
  fi
  warn "CRITICAL: database rollback reconciliation failed or database state could not be proven; keep all application services stopped and escalate"
  return 1
}


_tlr_restore_archive() {
  compose exec -T \
    --env "THREATLENS_RECOVERY_ROLE=${RESTORE_RECOVERY_ROLE}" \
    db sh -ceu '
      exec pg_restore --username "$THREATLENS_RECOVERY_ROLE" \
        --dbname "$POSTGRES_DB" --exit-on-error --single-transaction \
        --no-owner --no-privileges
    ' <"$1"
}


_tlr_restore_clear_redis() {
  compose exec -T redis sh -ceu '
    redis_call() {
      REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli --no-auth-warning -n 0 --raw "$@"
    }
    redis_config_get() {
      response="$(redis_call CONFIG GET "$1")" || return 1
      value="$(printf "%s\n" "$response" | sed -n "2p")"
      [ -n "$value" ] || return 1
      printf "%s\n" "$value"
    }

    appendonly="$(redis_config_get appendonly)" || exit 1
    [ "$appendonly" = yes ] || {
      printf "%s\n" "Redis AOF persistence is disabled; refusing a non-durable flush" >&2
      exit 1
    }
    previous_appendfsync="$(redis_config_get appendfsync)" || exit 1
    case "$previous_appendfsync" in
      always|everysec|no) ;;
      *)
        printf "%s\n" "Redis returned an unsupported appendfsync policy" >&2
        exit 1
        ;;
    esac

    appendfsync_changed=false
    restore_appendfsync() {
      original_status=$?
      trap - EXIT HUP INT TERM
      if [ "$appendfsync_changed" = true ]; then
        response="$(redis_call CONFIG SET appendfsync "$previous_appendfsync")" \
          && [ "$response" = OK ] \
          || return 1
      fi
      return "$original_status"
    }
    trap restore_appendfsync EXIT
    trap "exit 130" HUP INT TERM

    appendfsync_changed=true
    response="$(redis_call CONFIG SET appendfsync always)" || exit 1
    [ "$response" = OK ] || exit 1
    response="$(redis_call FLUSHDB)" || exit 1
    [ "$response" = OK ] || exit 1
    response="$(redis_call CONFIG SET appendfsync "$previous_appendfsync")" || exit 1
    [ "$response" = OK ] || exit 1
    appendfsync_changed=false
    trap - EXIT HUP INT TERM
  ' >/dev/null
}


_tlr_restore_run_packaged_upgrade() {
  local application_image="$1"
  local resource_suffix database_container smoke_status=0
  resource_suffix="$(python3 -c 'import secrets; print(secrets.token_hex(5))')" || return 1
  database_container="$(resolve_compose_container db)" || return 1
  RECOVERY_NETWORK="threatlens-recovery-app-${resource_suffix}"
  docker network create --internal --label threatlens.recovery=packaged-smoke \
    "${RECOVERY_NETWORK}" >/dev/null || return 1
  docker network connect --alias recovery-db "${RECOVERY_NETWORK}" \
    "${database_container}" >/dev/null || return 1
  # Consumed by the sourcing dispatcher's EXIT cleanup.
  # shellcheck disable=SC2034
  RECOVERY_DATABASE_CONTAINER_ATTACHED="${database_container}"

  if _tlr_drill_run_packaged_code_smoke \
    "${application_image}" "${RECOVERY_NETWORK}" recovery-db \
    "${SUPPORTED_DATABASE_NAME}" "${RESTORE_RECOVERY_ROLE}" \
    "${RESTORE_RECOVERY_PASSWORD}" "threatlens-recovery-api-${resource_suffix}"; then
    smoke_status=0
  else
    smoke_status=$?
  fi
  cleanup_recovery_network || return 1
  return "${smoke_status}"
}


_tlr_restore_finalize_database() {
  compose exec -T \
    --env "THREATLENS_RECOVERY_ROLE=${RESTORE_RECOVERY_ROLE}" \
    db sh -ceu '
      exec psql --no-psqlrc --set=ON_ERROR_STOP=1 \
        --username "$THREATLENS_RECOVERY_ROLE" --dbname "$POSTGRES_DB" \
        --set=app_role="$POSTGRES_USER" \
        --set=recovery_role="$THREATLENS_RECOVERY_ROLE"
  ' >/dev/null <<'SQL' || return 1
SELECT format('REASSIGN OWNED BY %I TO %I', :'recovery_role', :'app_role') \gexec
SELECT format('DROP OWNED BY %I', :'recovery_role') \gexec
SQL
  _tlr_restore_set_phase object_ownership_reassigned || return 1

  compose exec -T \
    --env "THREATLENS_RECOVERY_ROLE=${RESTORE_RECOVERY_ROLE}" \
    --env "THREATLENS_ROLLBACK_DB=${RESTORE_ROLLBACK_DATABASE}" \
    --env "THREATLENS_ORIGINAL_ROLE_CAN_LOGIN=${RESTORE_ORIGINAL_ROLE_CAN_LOGIN}" \
    db sh -ceu '
      exec psql --no-psqlrc --set=ON_ERROR_STOP=1 \
        --username "$THREATLENS_RECOVERY_ROLE" --dbname postgres \
        --set=db_name="$POSTGRES_DB" --set=app_role="$POSTGRES_USER" \
        --set=recovery_role="$THREATLENS_RECOVERY_ROLE" \
        --set=rollback_db="$THREATLENS_ROLLBACK_DB" \
        --set=original_role_can_login="$THREATLENS_ORIGINAL_ROLE_CAN_LOGIN"
    ' >/dev/null <<'SQL' || return 1
SELECT format('ALTER DATABASE %I WITH ALLOW_CONNECTIONS false', :'db_name') \gexec
SELECT pg_terminate_backend(pid)
FROM pg_catalog.pg_stat_activity
WHERE datname = :'db_name' AND pid <> pg_backend_pid();
SELECT format('ALTER DATABASE %I OWNER TO %I', :'db_name', :'app_role') \gexec
SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', :'db_name') \gexec
SELECT format('REVOKE ALL ON DATABASE %I FROM %I', :'db_name', :'recovery_role') \gexec
SELECT set_config('threatlens.recovery_target_database', :'db_name', false);
SELECT set_config('threatlens.recovery_source_database', :'rollback_db', false);
DO $acl$
DECLARE
  target_name text := current_setting('threatlens.recovery_target_database');
  source_name text := current_setting('threatlens.recovery_source_database');
  permission record;
  database_setting record;
  grantee_sql text;
  setting_name text;
  setting_value text;
BEGIN
  FOR permission IN
    SELECT exploded.grantee, exploded.privilege_type, exploded.is_grantable,
           role.rolname
    FROM pg_catalog.pg_database AS database
    CROSS JOIN LATERAL aclexplode(
      COALESCE(database.datacl, acldefault('d', database.datdba))
    ) AS exploded
    LEFT JOIN pg_catalog.pg_roles AS role ON role.oid = exploded.grantee
    WHERE database.datname = source_name
  LOOP
    grantee_sql := CASE
      WHEN permission.grantee = 0 THEN 'PUBLIC'
      WHEN permission.rolname IS NOT NULL THEN quote_ident(permission.rolname)
      ELSE NULL
    END;
    IF grantee_sql IS NULL THEN
      RAISE EXCEPTION 'rollback database ACL references an unknown role';
    END IF;
    EXECUTE format(
      'GRANT %s ON DATABASE %I TO %s%s',
      permission.privilege_type,
      target_name,
      grantee_sql,
      CASE WHEN permission.is_grantable THEN ' WITH GRANT OPTION' ELSE '' END
    );
  END LOOP;

  FOR database_setting IN
    SELECT settings.setrole, role.rolname, unnest(settings.setconfig) AS entry
    FROM pg_catalog.pg_database AS database
    JOIN pg_catalog.pg_db_role_setting AS settings ON settings.setdatabase = database.oid
    LEFT JOIN pg_catalog.pg_roles AS role ON role.oid = settings.setrole
    WHERE database.datname = source_name
  LOOP
    setting_name := split_part(database_setting.entry, '=', 1);
    setting_value := substr(
      database_setting.entry,
      length(setting_name) + 2
    );
    IF setting_name !~ '^[A-Za-z][A-Za-z0-9_.]*$' THEN
      RAISE EXCEPTION 'rollback database contains an unsupported setting name';
    END IF;
    IF database_setting.setrole = 0 THEN
      EXECUTE format(
        'ALTER DATABASE %I SET %I TO %L',
        target_name, setting_name, setting_value
      );
    ELSIF database_setting.rolname IS NOT NULL THEN
      EXECUTE format(
        'ALTER ROLE %I IN DATABASE %I SET %I TO %L',
        database_setting.rolname, target_name, setting_name, setting_value
      );
    ELSE
      RAISE EXCEPTION 'rollback database setting references an unknown role';
    END IF;
  END LOOP;
END
$acl$;
SELECT format(
  'ALTER ROLE %I %s',
  :'app_role', CASE WHEN :'original_role_can_login' = 'true' THEN 'LOGIN' ELSE 'NOLOGIN' END
) \gexec
SELECT format('ALTER ROLE %I NOLOGIN', :'recovery_role') \gexec
SQL
  _tlr_restore_set_phase application_access_restored || return 1

  compose exec -T \
    --env "THREATLENS_RECOVERY_ROLE=${RESTORE_RECOVERY_ROLE}" \
    --env "THREATLENS_ROLLBACK_DB=${RESTORE_ROLLBACK_DATABASE}" \
    --env "THREATLENS_ORIGINAL_DATABASE_ALLOW_CONNECTIONS=${RESTORE_ORIGINAL_DATABASE_ALLOW_CONNECTIONS}" \
    db sh -ceu '
      exec psql --no-psqlrc --set=ON_ERROR_STOP=1 \
        --username "$POSTGRES_USER" --dbname postgres \
        --set=db_name="$POSTGRES_DB" \
        --set=recovery_role="$THREATLENS_RECOVERY_ROLE" \
        --set=rollback_db="$THREATLENS_ROLLBACK_DB" \
        --set=original_database_allow_connections="$THREATLENS_ORIGINAL_DATABASE_ALLOW_CONNECTIONS"
    ' >/dev/null <<'SQL' || return 1
SELECT format('DROP ROLE %I', :'recovery_role') \gexec
SELECT format(
  'ALTER DATABASE %I WITH ALLOW_CONNECTIONS %s',
  :'db_name', :'original_database_allow_connections'
) \gexec
SQL
  _tlr_restore_set_phase connectivity_restored || return 1

  compose exec -T db sh -ceu '
    psql --no-psqlrc --set=ON_ERROR_STOP=1 --quiet \
      --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --command "SELECT 1" \
      >/dev/null
  ' || return 1

  local finalized_state
  finalized_state="$(compose exec -T \
    --env "THREATLENS_ROLLBACK_DB=${RESTORE_ROLLBACK_DATABASE}" \
    --env "THREATLENS_RECOVERY_ROLE=${RESTORE_RECOVERY_ROLE}" \
    db sh -ceu '
      exec psql --no-psqlrc --set=ON_ERROR_STOP=1 --tuples-only --no-align \
        --username "$POSTGRES_USER" --dbname postgres \
        --set=db_name="$POSTGRES_DB" \
        --set=rollback_db="$THREATLENS_ROLLBACK_DB" \
        --set=recovery_role="$THREATLENS_RECOVERY_ROLE"
    ' <<'SQL' | tr -d '[:space:]'
SELECT concat_ws('|',
       CASE WHEN role.rolcanlogin THEN '1' ELSE '0' END,
       CASE WHEN database.datallowconn THEN '1' ELSE '0' END,
       CASE WHEN database.datdba = role.oid THEN '1' ELSE '0' END,
       CASE WHEN EXISTS (
         SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = :'recovery_role'
       ) THEN '1' ELSE '0' END,
       CASE WHEN EXISTS (
         SELECT 1 FROM pg_catalog.pg_database WHERE datname = :'rollback_db'
       ) THEN '1' ELSE '0' END)
FROM pg_catalog.pg_roles AS role
JOIN pg_catalog.pg_database AS database ON database.datname = :'db_name'
WHERE role.rolname = current_user;
SQL
  )" || return 1
  [[ "${finalized_state}" == "1|1|1|0|1" ]] || return 1
  _tlr_restore_set_phase replacement_verified || return 1

  _tlr_restore_set_phase forward_commit_requested || return 1

  compose exec -T \
    --env "THREATLENS_ROLLBACK_DB=${RESTORE_ROLLBACK_DATABASE}" \
    db sh -ceu '
      exec psql --no-psqlrc --set=ON_ERROR_STOP=1 --username "$POSTGRES_USER" \
        --dbname postgres --set=rollback_db="$THREATLENS_ROLLBACK_DB"
    ' >/dev/null <<'SQL' || return 1
SELECT format('DROP DATABASE %I', :'rollback_db') \gexec
SQL
  _tlr_restore_set_phase rollback_database_removed || return 1

  local rollback_state
  rollback_state="$(_tlr_restore_database_state "${RESTORE_ROLLBACK_DATABASE}")" || return 1
  [[ "${rollback_state}" == "missing" ]] || return 1

  _tlr_restore_set_phase completed || return 1
  if [[ "${THREATLENS_RECOVERY_RESTORE_TEST_FAILPOINT:-}" == "after_completed_phase" ]]; then
    kill -KILL "$$"
  fi
  RESTORE_REPLACEMENT_ACTIVE=false
  RESTORE_RECOVERY_PASSWORD=""
}


_tlr_restore_run_isolated_preflight() {
  local timeout_seconds="$1"
  local database_image="$2"
  local application_image="$3"
  local hook="$4"
  local status=0
  if tlr_run_isolated_drill \
    "${timeout_seconds}" "${database_image}" "${application_image}" "${hook}"; then
    status=0
  else
    status=$?
  fi
  cleanup_drill_resources || return 99
  return "${status}"
}


tlr_restore_command() {
  local backup="" confirmation="" show_confirmation=false
  local acknowledge_data_loss=false acknowledge_encryption_mismatch=false
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
      --show-confirmation)
        show_confirmation=true
        shift
        ;;
      --confirm)
        (($# >= 2)) || die "${EXIT_USAGE}" "E215" "--confirm requires the target-bound confirmation text"
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
      --acknowledge-encryption-key-mismatch)
        acknowledge_encryption_mismatch=true
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
  [[ -n "${COMPOSE_PROJECT}" ]] \
    || die "${EXIT_REFUSED}" "E711" \
      "Destructive restore requires an explicit --project-name or THREATLENS_COMPOSE_PROJECT"
  acquire_recovery_operation_lock

  local expected_version=""
  if [[ "${allow_version_mismatch}" != true ]]; then
    expected_version="$(resolve_app_version)"
  fi
  prepare_verified_archive "${backup}" "${expected_version}"
  require_encryption_fingerprint_match "${acknowledge_encryption_mismatch}"
  validate_supported_local_targets
  validate_running_target_configuration
  require_compose_services db redis api worker worker-ai worker-maintenance worker-notifications beat web
  require_running_service db
  require_running_service redis
  require_database_ready
  resolve_target_deployment_identity "${VERIFIED_ARCHIVE_SHA256}"
  local confirmed_config_sha256="${TARGET_CONFIG_SHA256}"
  local confirmed_deployment_identity="${TARGET_DEPLOYMENT_IDENTITY}"

  local expected_confirmation
  expected_confirmation="$(restore_confirmation_text)"
  if [[ "${show_confirmation}" == true ]]; then
    # These operation-ledger globals belong to the sourcing dispatcher.
    # shellcheck disable=SC2034
    OPERATION_TYPE=""
    # shellcheck disable=SC2034
    OPERATION_FINISHED=true
    printf '%s\n' "${expected_confirmation}"
    return 0
  fi
  [[ "${confirmation}" == "${expected_confirmation}" ]] \
    || die "${EXIT_REFUSED}" "E704" \
      "Destructive restore refused; run restore --show-confirmation for the exact live target-bound text"
  [[ "${acknowledge_data_loss}" == true ]] \
    || die "${EXIT_REFUSED}" "E705" \
      "Destructive restore refused; --acknowledge-data-loss is also required"

  quarantine_hook="$(_tlr_restore_resolve_hook "${quarantine_hook}")"
  stage_restore_inputs "${quarantine_hook}"
  quarantine_hook="${STAGED_QUARANTINE_HOOK}"
  local database_image application_image
  PINNED_DATABASE_IMAGE="$(resolve_database_image)"
  PINNED_APPLICATION_IMAGE="$(resolve_application_image)"
  database_image="${PINNED_DATABASE_IMAGE}"
  application_image="${PINNED_APPLICATION_IMAGE}"
  check_pg_restore_catalog "${VERIFIED_ARCHIVE}" "${database_image}"

  set_operation_metadata \
    --field tool_version=1 \
    --field "archive_sha256=${VERIFIED_ARCHIVE_SHA256}" \
    --field "app_version=${VERIFIED_APP_VERSION}" \
    --field "alembic_revision=${VERIFIED_ALEMBIC_REVISION}" \
    --field "archive_size_bytes=${VERIFIED_ARCHIVE_SIZE}" \
    --field redis_restored=false
  # shellcheck disable=SC2034
  OPERATION_LEDGER_ALLOWED=true

  log "Restoring the archive in isolation before any production mutation"
  local isolated_status=0
  if _tlr_restore_run_isolated_preflight \
    90 "${database_image}" "${application_image}" "${quarantine_hook}"; then
    isolated_status=0
  else
    isolated_status=$?
  fi
  [[ "${isolated_status}" == 0 ]] \
    || die "${EXIT_REFUSED}" "E706" \
      "Isolated restore, packaged migration/API smoke, or quarantine preflight failed at stage ${isolated_status}; production was not changed"

  log "Creating mandatory fresh safety backup before destructive restore"
  BACKUP_ERROR_CONTEXT=restore_safety
  perform_backup "${safety_backup_directory}" >/dev/null
  # Consumed by the sourcing dispatcher's error handler.
  # shellcheck disable=SC2034
  BACKUP_ERROR_CONTEXT=""
  local safety_backup="${COMPLETED_BACKUP_DIRECTORY}"

  _tlr_restore_stop_application_services
  require_running_service db
  revalidate_live_target_identity "${confirmed_config_sha256}" \
    "${confirmed_deployment_identity}" "${VERIFIED_ARCHIVE_SHA256}"
  _tlr_restore_capture_original_access_state \
    || die "${EXIT_RESTORE}" "E816" "Unable to prove the original database access state before fencing"

  local rollback_database random_suffix recovery_role recovery_password
  random_suffix="$(python3 -c 'import secrets; print(secrets.token_hex(6))')"
  rollback_database="tl_pre_restore_$(date -u +'%Y%m%dT%H%M%SZ')_${random_suffix}"
  recovery_role="tl_recovery_${random_suffix}"
  recovery_password="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
  RESTORE_ROLLBACK_DATABASE="${rollback_database}"
  RESTORE_RECOVERY_ROLE="${recovery_role}"
  RESTORE_RECOVERY_PASSWORD="${recovery_password}"
  _tlr_restore_journal_init \
    || die "${EXIT_REFUSED}" "E718" \
      "Destructive restore refused because a durable active journal could not be created; reconcile any unfinished operation first"
  _tlr_restore_arm_rollback "${rollback_database}" "${recovery_role}" "${recovery_password}"
  _tlr_restore_set_phase fence_requested \
    || die "${EXIT_RESTORE}" "E824" "Unable to durably journal the database-fencing intent"

  log "Fencing database access, preserving the original database, and creating a clean target"
  _tlr_restore_create_clean_database \
    || die "${EXIT_RESTORE}" "E805" \
      "Clean-target creation failed; rollback reconciliation was attempted and services remain stopped"
  RESTORE_REPLACEMENT_DATABASE_OID="$(_tlr_restore_database_oid "${SUPPORTED_DATABASE_NAME}")" \
    || die "${EXIT_RESTORE}" "E825" "Unable to capture the replacement database identity"
  _tlr_restore_set_phase clean_target_created \
    --replacement-database-oid "${RESTORE_REPLACEMENT_DATABASE_OID}" \
    || die "${EXIT_RESTORE}" "E826" "Unable to durably journal the replacement database identity"

  revalidate_staged_restore_inputs \
    || die "${EXIT_RESTORE}" "E817" "Privately staged restore inputs changed before database restore"
  _tlr_restore_archive "${VERIFIED_ARCHIVE}" \
    || die "${EXIT_RESTORE}" "E806" \
      "Archive restore failed; rollback reconciliation was attempted and services remain stopped"
  _tlr_restore_set_phase archive_restored \
    || die "${EXIT_RESTORE}" "E827" "Unable to durably journal the archive restore"

  log "Applying packaged migrations and API/schema smoke checks while database access remains fenced"
  _tlr_restore_run_packaged_upgrade "${application_image}" \
    || die "${EXIT_RESTORE}" "E818" \
      "Packaged migration or API/schema smoke failed; rollback reconciliation was attempted"
  _tlr_restore_set_phase schema_upgraded \
    || die "${EXIT_RESTORE}" "E828" "Unable to durably journal the schema upgrade"

  revalidate_staged_restore_inputs \
    || die "${EXIT_RESTORE}" "E819" "Privately staged hook or manifest changed before quarantine"
  _tlr_restore_run_hook "${quarantine_hook}" preflight \
    || die "${EXIT_RESTORE}" "E820" \
      "Quarantine preflight failed on the restored target; rollback reconciliation was attempted"
  revalidate_staged_restore_inputs \
    || die "${EXIT_RESTORE}" "E821" "Privately staged hook or manifest changed before quarantine apply"
  _tlr_restore_run_hook "${quarantine_hook}" apply \
    || die "${EXIT_RESTORE}" "E808" \
      "Restore quarantine failed; rollback reconciliation was attempted and services remain stopped"
  revalidate_staged_restore_inputs \
    || die "${EXIT_RESTORE}" "E822" "Privately staged hook or manifest changed before quarantine verification"
  _tlr_restore_run_hook "${quarantine_hook}" verify \
    || die "${EXIT_RESTORE}" "E809" \
      "Restore quarantine could not be verified; rollback reconciliation was attempted"
  _tlr_restore_set_phase quarantine_verified \
    || die "${EXIT_RESTORE}" "E829" "Unable to durably journal quarantine verification"

  if ! compose up --detach redis >/dev/null; then
    die "${EXIT_RESTORE}" "E810" \
      "Redis could not be made available for clearing; rollback reconciliation was attempted"
  fi
  _tlr_restore_clear_redis \
    || die "${EXIT_RESTORE}" "E811" \
      "Redis database 0 could not be cleared; rollback reconciliation was attempted"
  _tlr_restore_set_phase redis_cleared \
    || die "${EXIT_RESTORE}" "E830" "Unable to durably journal Redis clearing"

  _tlr_restore_finalize_database \
    || die "${EXIT_RESTORE}" "E812" \
      "Restore quarantine succeeded, but database ACL/connectivity finalization failed; reconciliation was attempted"
  RESTORE_FORWARD_COMMITTED=true
  _tlr_restore_journal_terminal succeeded forward_committed "" \
    || die "${EXIT_RESTORE}" "E831" \
      "Restore committed, but its terminal host journal could not be persisted"

  set_operation_metadata \
    --field tool_version=1 \
    --field "archive_sha256=${VERIFIED_ARCHIVE_SHA256}" \
    --field "app_version=${VERIFIED_APP_VERSION}" \
    --field "alembic_revision=${DRILL_UPGRADED_REVISION}" \
    --field reconciled_forward=false \
    --field outbound_quarantined=true \
    --field redis_restored=false
  # shellcheck disable=SC2034
  OPERATION_LEDGER_ALLOWED=true

  log "Destructive restore completed; outbound work is quarantined, Redis database 0 was cleared, and services remain stopped"
  printf 'RESTORE_STATUS=completed_quarantined\nSAFETY_BACKUP=%s\n' "${safety_backup}"
  printf '%s\n' \
    "Review the quarantine evidence, then start only the services you intend to resume." >&2
}


tlr_reconcile_command() {
  (($# == 0)) || die "${EXIT_USAGE}" "E230" "reconcile does not accept command options"
  [[ -n "${COMPOSE_PROJECT}" ]] \
    || die "${EXIT_REFUSED}" "E711" \
      "Recovery reconciliation requires an explicit --project-name or THREATLENS_COMPOSE_PROJECT"
  acquire_recovery_operation_lock

  validate_supported_local_targets
  validate_running_target_configuration
  require_running_service db
  require_running_service redis
  require_database_ready
  local current_config_sha256="${TARGET_CONFIG_SHA256}"
  _tlr_restore_load_journal \
    || die "${EXIT_REFUSED}" "E719" \
      "The active recovery journal is unavailable, unsafe, malformed, or does not match this project"
  if [[ "${RESTORE_JOURNAL_STATUS}" == "succeeded" \
    || "${RESTORE_JOURNAL_STATUS}" == "failed" ]]; then
    _tlr_restore_ingest_terminal_journal \
      || die "${EXIT_RESTORE}" "E835" \
        "Terminal host journal could not be ingested into operation evidence; the journal was retained"
    printf 'RECONCILE_STATUS=evidence_ingested\n'
    return 0
  fi
  [[ "${RESTORE_JOURNAL_TARGET_CONFIG_SHA256}" == "${current_config_sha256}" ]] \
    || die "${EXIT_REFUSED}" "E720" \
      "Current rendered configuration does not match the interrupted recovery journal"
  local journal_identity="${TARGET_DEPLOYMENT_IDENTITY}"
  resolve_target_deployment_identity "${VERIFIED_ARCHIVE_SHA256}"
  [[ "${TARGET_DEPLOYMENT_IDENTITY}" == "${journal_identity}" \
    && "${TARGET_CONFIG_SHA256}" == "${current_config_sha256}" ]] \
    || die "${EXIT_REFUSED}" "E720" \
      "Current live target identity does not match the interrupted recovery journal"

  _tlr_restore_stop_application_services
  revalidate_live_target_identity "${current_config_sha256}" \
    "${journal_identity}" "${VERIFIED_ARCHIVE_SHA256}"
  RESTORE_REPLACEMENT_ACTIVE=true
  if ! _tlr_restore_emergency_rollback; then
    RESTORE_REPLACEMENT_ACTIVE=false
    die "${EXIT_RESTORE}" "E832" \
      "Interrupted recovery remains in an unknown state; no operation evidence was ingested"
  fi

  set_operation_metadata \
    --field tool_version=1 \
    --field "archive_sha256=${VERIFIED_ARCHIVE_SHA256}" \
    --field redis_restored=false \
    --field reconciled_after_interruption=true
  # Consumed by the sourcing dispatcher's operation ledger.
  # shellcheck disable=SC2034
  OPERATION_LEDGER_ALLOWED=true
  if [[ "${RESTORE_FORWARD_COMMITTED}" == true ]]; then
    set_operation_metadata \
      --field tool_version=1 \
      --field "archive_sha256=${VERIFIED_ARCHIVE_SHA256}" \
      --field outbound_quarantined=true \
      --field redis_restored=false \
      --field reconciled_after_interruption=true \
      --field reconciled_forward=true
    _tlr_restore_journal_terminal succeeded forward_committed "" \
      || die "${EXIT_RESTORE}" "E833" "Forward-commit reconciliation could not persist its terminal journal"
    finish_operation_best_effort succeeded
    archive_restore_journal_best_effort
    printf 'RECONCILE_STATUS=forward_committed\n'
    return 0
  fi

  _tlr_restore_journal_terminal failed rolled_back E_INTERRUPTED_RECONCILED \
    || die "${EXIT_RESTORE}" "E834" "Rollback reconciliation could not persist its terminal journal"
  finish_operation_best_effort failed E_INTERRUPTED_RECONCILED
  archive_restore_journal_best_effort
  printf 'RECONCILE_STATUS=rolled_back\n'
}
