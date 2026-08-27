#!/usr/bin/env bash

# Container-side shell fragments intentionally defer variable expansion.
# shellcheck disable=SC2016

set -Eeuo pipefail
IFS=$'\n\t'
umask 077

readonly EXIT_USAGE=2
readonly EXIT_PREREQUISITE=3
readonly EXIT_APPLY=5
readonly EXIT_VERIFY=6

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
MANIFEST_HELPER="${SCRIPT_DIR}/recovery_manifest.py"
ENV_FILE="${THREATLENS_RECOVERY_ENV_FILE:-}"
PROJECT_NAME="${THREATLENS_RECOVERY_PROJECT_NAME:-}"
MANIFEST_PATH="${THREATLENS_RECOVERY_MANIFEST:-}"
VERIFIED_ARCHIVE_SHA256="${THREATLENS_RECOVERY_ARCHIVE_SHA256:-}"
declare -a COMPOSE_FILES=()
declare -a COMPOSE_COMMAND=()


fail() {
  local exit_code="$1"
  local error_code="$2"
  shift 2
  printf 'ERROR [%s] %s\n' "${error_code}" "$*" >&2
  exit "${exit_code}"
}


require_command() {
  command -v "$1" >/dev/null 2>&1 \
    || fail "${EXIT_PREREQUISITE}" "Q301" "Required command is unavailable: $1"
}


initialize() {
  local phase="$1"
  [[ "${phase}" =~ ^(preflight|apply|verify)$ ]] \
    || fail "${EXIT_USAGE}" "Q201" "Phase must be preflight, apply, or verify"
  if [[ -n "${THREATLENS_RECOVERY_PHASE:-}" && "${THREATLENS_RECOVERY_PHASE}" != "${phase}" ]]; then
    fail "${EXIT_USAGE}" "Q202" "Positional and environment phases do not match"
  fi
  ((BASH_VERSINFO[0] >= 4)) \
    || fail "${EXIT_PREREQUISITE}" "Q315" "Bash 4 or newer is required"
  require_command docker
  require_command python3
  python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' \
    || fail "${EXIT_PREREQUISITE}" "Q316" "Python 3.10 or newer is required"
  [[ -r "${ENV_FILE}" && -f "${ENV_FILE}" && ! -L "${ENV_FILE}" ]] \
    || fail "${EXIT_PREREQUISITE}" "Q302" "Recovery environment file is unavailable or unsafe"
  [[ -n "${MANIFEST_PATH}" ]] \
    || fail "${EXIT_PREREQUISITE}" "Q303" "Recovery manifest was not provided"

  mapfile -t COMPOSE_FILES <<<"${THREATLENS_RECOVERY_COMPOSE_FILES:-}"
  ((${#COMPOSE_FILES[@]} > 0)) \
    || fail "${EXIT_PREREQUISITE}" "Q304" "No Compose files were provided"
  COMPOSE_COMMAND=(docker compose --env-file "${ENV_FILE}")
  local compose_file
  for compose_file in "${COMPOSE_FILES[@]}"; do
    [[ -r "${compose_file}" && -f "${compose_file}" && ! -L "${compose_file}" ]] \
      || fail "${EXIT_PREREQUISITE}" "Q305" "A recovery Compose file is unavailable or unsafe"
    COMPOSE_COMMAND+=(-f "${compose_file}")
  done
  if [[ -n "${PROJECT_NAME}" ]]; then
    [[ "${PROJECT_NAME}" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] \
      || fail "${EXIT_PREREQUISITE}" "Q306" "Compose project name is invalid"
    COMPOSE_COMMAND+=(--project-name "${PROJECT_NAME}")
  fi
  docker compose version >/dev/null 2>&1 \
    || fail "${EXIT_PREREQUISITE}" "Q307" "Docker Compose v2 is required"
  docker info >/dev/null 2>&1 \
    || fail "${EXIT_PREREQUISITE}" "Q308" "Docker daemon is unavailable"
  local services running
  services="$(compose config --services)" \
    || fail "${EXIT_PREREQUISITE}" "Q309" "Compose configuration could not be rendered"
  grep -Fxq db <<<"${services}" \
    || fail "${EXIT_PREREQUISITE}" "Q310" "Compose service db is unavailable"
  running="$(compose ps --services --status running)" \
    || fail "${EXIT_PREREQUISITE}" "Q311" "Running Compose services could not be inspected"
  grep -Fxq db <<<"${running}" \
    || fail "${EXIT_PREREQUISITE}" "Q312" "Compose service db is not running"
}


compose() {
  "${COMPOSE_COMMAND[@]}" "$@"
}


manifest_field() {
  python3 "${MANIFEST_HELPER}" field --backup "${MANIFEST_PATH}" --name "$1"
}


archive_checksum() {
  if [[ -z "${VERIFIED_ARCHIVE_SHA256}" ]]; then
    manifest_field archive_sha256
    return
  fi
  [[ "${VERIFIED_ARCHIVE_SHA256}" =~ ^[0-9a-f]{64}$ ]] || return 1
  local declared_checksum
  declared_checksum="$(python3 "${MANIFEST_HELPER}" declared-field \
    --backup "${MANIFEST_PATH}" --name archive_sha256 2>/dev/null)" || return 1
  [[ "${VERIFIED_ARCHIVE_SHA256}" == "${declared_checksum}" ]] || return 1
  printf '%s\n' "${VERIFIED_ARCHIVE_SHA256}"
}


run_psql() {
  compose exec -T db sh -ceu '
    exec psql --no-psqlrc --set=ON_ERROR_STOP=1 --quiet \
      --username "$POSTGRES_USER" --dbname "$POSTGRES_DB"
  '
}


preflight() {
  archive_checksum >/dev/null \
    || fail "${EXIT_PREREQUISITE}" "Q313" "Recovery manifest validation failed"
  if ! run_psql >/dev/null <<'SQL'
DO $preflight$
DECLARE
  missing_columns text;
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_catalog.pg_roles
    WHERE rolname = current_user AND (rolsuper OR rolcreatedb)
  ) THEN
    RAISE EXCEPTION 'recovery database role requires CREATEDB or superuser capability';
  END IF;
  IF NOT EXISTS (
    SELECT 1
    FROM pg_catalog.pg_database AS database
    JOIN pg_catalog.pg_roles AS role ON role.rolname = current_user
    WHERE database.datname = current_database()
      AND (role.rolsuper OR database.datdba = role.oid)
  ) THEN
    RAISE EXCEPTION 'recovery database role must own the application database';
  END IF;
  IF to_regclass('public.users') IS NULL THEN
    RAISE EXCEPTION 'required recovery relation users is missing';
  END IF;
  IF to_regclass('public.api_tokens') IS NULL THEN
    RAISE EXCEPTION 'required recovery relation api_tokens is missing';
  END IF;
  IF to_regclass('public.audit_logs') IS NULL THEN
    RAISE EXCEPTION 'required recovery relation audit_logs is missing';
  END IF;

  SELECT string_agg(required.column_name, ', ' ORDER BY required.column_name)
  INTO missing_columns
  FROM (VALUES
    ('users', 'auth_token_version'),
    ('api_tokens', 'revoked_at'),
    ('audit_logs', 'id'),
    ('audit_logs', 'action'),
    ('audit_logs', 'resource_type'),
    ('audit_logs', 'resource_id'),
    ('audit_logs', 'success'),
    ('audit_logs', 'metadata_json')
  ) AS required(table_name, column_name)
  WHERE NOT EXISTS (
    SELECT 1
    FROM information_schema.columns AS existing
    WHERE existing.table_schema = 'public'
      AND existing.table_name = required.table_name
      AND existing.column_name = required.column_name
  );
  IF missing_columns IS NOT NULL THEN
    RAISE EXCEPTION 'required recovery columns are missing: %', missing_columns;
  END IF;

  SELECT string_agg(
    required.table_name || '.' || required.column_name,
    ', ' ORDER BY required.table_name, required.column_name
  )
  INTO missing_columns
  FROM (VALUES
    ('auth_sessions', 'revoked_at'),
    ('mfa_login_challenges', 'consumed_at'),
    ('integration_instances', 'enabled'),
    ('integration_instances', 'last_error_at'),
    ('integration_instances', 'last_error'),
    ('integration_subscriptions', 'enabled'),
    ('notification_webhooks', 'enabled'),
    ('integration_events', 'routing_state'),
    ('integration_events', 'claimed_at'),
    ('integration_events', 'last_error'),
    ('integration_deliveries', 'state'),
    ('integration_deliveries', 'claimed_at'),
    ('integration_deliveries', 'not_before'),
    ('integration_deliveries', 'completed_at'),
    ('integration_deliveries', 'dead_lettered_at'),
    ('integration_deliveries', 'last_error_code'),
    ('integration_deliveries', 'last_error_message'),
    ('integration_deliveries', 'last_error_retryable'),
    ('integration_attempts', 'status'),
    ('integration_attempts', 'finished_at'),
    ('integration_attempts', 'error_code'),
    ('integration_attempts', 'error_message'),
    ('integration_attempts', 'retryable'),
    ('notification_webhook_deliveries', 'delivery_state'),
    ('notification_webhook_deliveries', 'success'),
    ('notification_webhook_deliveries', 'claimed_at'),
    ('notification_webhook_deliveries', 'error'),
    ('report_schedules', 'enabled'),
    ('report_schedules', 'delivery_enabled'),
    ('report_schedules', 'next_run_at'),
    ('reports', 'delivery_requested')
  ) AS required(table_name, column_name)
  WHERE to_regclass('public.' || required.table_name) IS NOT NULL
    AND NOT EXISTS (
      SELECT 1
      FROM information_schema.columns AS existing
      WHERE existing.table_schema = 'public'
        AND existing.table_name = required.table_name
        AND existing.column_name = required.column_name
    );
  IF missing_columns IS NOT NULL THEN
    RAISE EXCEPTION 'optional recovery relations have unsupported columns: %', missing_columns;
  END IF;

  IF to_regclass('public.auth_sessions') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'auth_sessions' AND column_name = 'revoked_at'
  ) THEN
    RAISE EXCEPTION 'auth_sessions exists without a supported revoked_at column';
  END IF;
  IF to_regclass('public.mfa_login_challenges') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'mfa_login_challenges'
      AND column_name = 'consumed_at'
  ) THEN
    RAISE EXCEPTION 'mfa_login_challenges exists without a supported consumed_at column';
  END IF;
  IF to_regclass('public.integration_instances') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'integration_instances' AND column_name = 'enabled'
  ) THEN
    RAISE EXCEPTION 'integration_instances exists without a supported enabled column';
  END IF;
  IF to_regclass('public.integration_instances') IS NOT NULL AND EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'integration_instances' AND column_name = 'circuit_state'
  ) AND NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'integration_instances' AND column_name = 'circuit_open_until'
  ) THEN
    RAISE EXCEPTION 'integration_instances circuit columns are incomplete';
  END IF;
  IF to_regclass('public.integration_subscriptions') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'integration_subscriptions' AND column_name = 'enabled'
  ) THEN
    RAISE EXCEPTION 'integration_subscriptions exists without a supported enabled column';
  END IF;
  IF to_regclass('public.notification_webhooks') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'notification_webhooks' AND column_name = 'enabled'
  ) THEN
    RAISE EXCEPTION 'notification_webhooks exists without a supported enabled column';
  END IF;
  IF to_regclass('public.report_schedules') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'report_schedules' AND column_name = 'delivery_enabled'
  ) THEN
    RAISE EXCEPTION 'report_schedules exists without a supported delivery_enabled column';
  END IF;
  IF to_regclass('public.report_schedules') IS NOT NULL AND EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'report_schedules' AND column_name = 'failure_state'
  ) THEN
    SELECT string_agg(required.column_name, ', ' ORDER BY required.column_name)
    INTO missing_columns
    FROM (VALUES
      ('retry_at'), ('last_error_code'), ('last_error'), ('last_error_at')
    ) AS required(column_name)
    WHERE NOT EXISTS (
      SELECT 1 FROM information_schema.columns AS existing
      WHERE existing.table_schema = 'public'
        AND existing.table_name = 'report_schedules'
        AND existing.column_name = required.column_name
    );
    IF missing_columns IS NOT NULL THEN
      RAISE EXCEPTION 'report_schedules quarantine columns are incomplete: %', missing_columns;
    END IF;
  END IF;
END
$preflight$;
SQL
  then
    fail "${EXIT_PREREQUISITE}" "Q314" "Database schema cannot satisfy the quarantine contract"
  fi
  printf 'QUARANTINE_PREFLIGHT=passed\n'
}


apply_quarantine() {
  local archive_checksum audit_id
  archive_checksum="$(archive_checksum)" \
    || fail "${EXIT_APPLY}" "Q501" "Recovery manifest validation failed"
  audit_id="$(python3 -c 'import uuid; print(uuid.uuid4())')" \
    || fail "${EXIT_APPLY}" "Q502" "Audit identifier generation failed"

  if ! compose exec -T \
    --env "THREATLENS_RESTORE_CHECKSUM=${archive_checksum}" \
    --env "THREATLENS_RESTORE_AUDIT_ID=${audit_id}" \
    db sh -ceu '
      exec psql --no-psqlrc --set=ON_ERROR_STOP=1 --quiet \
        --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
        --set=restore_checksum="$THREATLENS_RESTORE_CHECKSUM" \
        --set=audit_id="$THREATLENS_RESTORE_AUDIT_ID"
    ' >/dev/null <<'SQL'
BEGIN;
SELECT pg_advisory_xact_lock(721304819352680117);
SELECT set_config('threatlens.restore_checksum', :'restore_checksum', true);
SELECT set_config('threatlens.restore_audit_id', :'audit_id', true);

DO $quarantine$
DECLARE
  affected_users bigint := 0;
  revoked_api_tokens bigint := 0;
  revoked_sessions bigint := 0;
  consumed_mfa_challenges bigint := 0;
  disabled_instances bigint := 0;
  disabled_subscriptions bigint := 0;
  disabled_webhooks bigint := 0;
  quarantined_events bigint := 0;
  quarantined_deliveries bigint := 0;
  interrupted_attempts bigint := 0;
  quarantined_legacy_deliveries bigint := 0;
  disabled_schedules bigint := 0;
  disabled_report_deliveries bigint := 0;
BEGIN
  IF EXISTS (
    SELECT 1 FROM audit_logs
    WHERE action = 'system.restore.quarantine'
      AND resource_type = 'postgresql_backup'
      AND resource_id = current_setting('threatlens.restore_checksum')
      AND success IS TRUE
  ) THEN
    RETURN;
  END IF;

  UPDATE users
  SET auth_token_version = auth_token_version + 1;
  GET DIAGNOSTICS affected_users = ROW_COUNT;

  UPDATE api_tokens
  SET revoked_at = COALESCE(revoked_at, clock_timestamp())
  WHERE revoked_at IS NULL;
  GET DIAGNOSTICS revoked_api_tokens = ROW_COUNT;

  IF to_regclass('public.auth_sessions') IS NOT NULL THEN
    IF EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_schema = 'public' AND table_name = 'auth_sessions'
        AND column_name = 'revoked_reason'
    ) THEN
      EXECUTE $sql$UPDATE auth_sessions
        SET revoked_at = COALESCE(revoked_at, clock_timestamp()),
            revoked_reason = COALESCE(revoked_reason, 'restore_quarantine')
        WHERE revoked_at IS NULL$sql$;
    ELSE
      EXECUTE 'UPDATE auth_sessions SET revoked_at = COALESCE(revoked_at, clock_timestamp()) WHERE revoked_at IS NULL';
    END IF;
    GET DIAGNOSTICS revoked_sessions = ROW_COUNT;
  END IF;

  IF to_regclass('public.mfa_login_challenges') IS NOT NULL THEN
    EXECUTE $sql$UPDATE mfa_login_challenges
      SET consumed_at = COALESCE(consumed_at, clock_timestamp())
      WHERE consumed_at IS NULL$sql$;
    GET DIAGNOSTICS consumed_mfa_challenges = ROW_COUNT;
  END IF;

  IF to_regclass('public.integration_instances') IS NOT NULL THEN
    EXECUTE $sql$UPDATE integration_instances
      SET enabled = false,
          last_error_at = clock_timestamp(),
          last_error = 'Quarantined after disaster recovery restore.'
      WHERE enabled IS TRUE$sql$;
    GET DIAGNOSTICS disabled_instances = ROW_COUNT;
    IF EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_schema = 'public' AND table_name = 'integration_instances'
        AND column_name = 'circuit_state'
    ) THEN
      EXECUTE $sql$UPDATE integration_instances
        SET circuit_state = 'open', circuit_open_until = NULL
        WHERE circuit_state <> 'open' OR circuit_open_until IS NOT NULL$sql$;
    END IF;
  END IF;

  IF to_regclass('public.integration_subscriptions') IS NOT NULL THEN
    EXECUTE 'UPDATE integration_subscriptions SET enabled = false WHERE enabled IS TRUE';
    GET DIAGNOSTICS disabled_subscriptions = ROW_COUNT;
  END IF;

  IF to_regclass('public.notification_webhooks') IS NOT NULL THEN
    EXECUTE 'UPDATE notification_webhooks SET enabled = false WHERE enabled IS TRUE';
    GET DIAGNOSTICS disabled_webhooks = ROW_COUNT;
  END IF;

  IF to_regclass('public.integration_events') IS NOT NULL THEN
    EXECUTE $sql$UPDATE integration_events
      SET routing_state = 'dead_letter', claimed_at = NULL,
          last_error = 'Quarantined after disaster recovery restore.'
      WHERE routing_state IN ('pending', 'routing', 'failed')$sql$;
    GET DIAGNOSTICS quarantined_events = ROW_COUNT;
  END IF;

  IF to_regclass('public.integration_deliveries') IS NOT NULL THEN
    EXECUTE $sql$UPDATE integration_deliveries
      SET state = 'dead_letter', claimed_at = NULL, not_before = NULL,
          completed_at = COALESCE(completed_at, clock_timestamp()),
          dead_lettered_at = COALESCE(dead_lettered_at, clock_timestamp()),
          last_error_code = 'restore_quarantine',
          last_error_message = 'Quarantined after disaster recovery restore.',
          last_error_retryable = false
      WHERE state IN ('pending', 'sending', 'retry_wait')$sql$;
    GET DIAGNOSTICS quarantined_deliveries = ROW_COUNT;
  END IF;

  IF to_regclass('public.integration_attempts') IS NOT NULL THEN
    EXECUTE $sql$UPDATE integration_attempts
      SET status = 'interrupted', finished_at = COALESCE(finished_at, clock_timestamp()),
          error_code = 'restore_quarantine',
          error_message = 'Interrupted by disaster recovery restore.', retryable = false
      WHERE status = 'running'$sql$;
    GET DIAGNOSTICS interrupted_attempts = ROW_COUNT;
  END IF;

  IF to_regclass('public.notification_webhook_deliveries') IS NOT NULL AND EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'notification_webhook_deliveries'
      AND column_name = 'delivery_state'
  ) THEN
    IF EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_schema = 'public' AND table_name = 'notification_webhook_deliveries'
        AND column_name = 'not_before'
    ) THEN
      EXECUTE $sql$UPDATE notification_webhook_deliveries
        SET delivery_state = 'failed', success = false, claimed_at = NULL,
            not_before = NULL, error = 'Quarantined after disaster recovery restore.'
        WHERE delivery_state IN ('pending', 'sending', 'retry_wait')$sql$;
    ELSE
      EXECUTE $sql$UPDATE notification_webhook_deliveries
        SET delivery_state = 'failed', success = false, claimed_at = NULL,
            error = 'Quarantined after disaster recovery restore.'
        WHERE delivery_state IN ('pending', 'sending', 'retry_wait')$sql$;
    END IF;
    GET DIAGNOSTICS quarantined_legacy_deliveries = ROW_COUNT;
  END IF;

  IF to_regclass('public.report_schedules') IS NOT NULL THEN
    IF EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_schema = 'public' AND table_name = 'report_schedules'
        AND column_name = 'failure_state'
    ) THEN
      EXECUTE $sql$UPDATE report_schedules
        SET enabled = false, delivery_enabled = false, next_run_at = NULL,
            retry_at = NULL, failure_state = 'quarantined',
            last_error_code = 'restore_quarantine',
            last_error = 'Quarantined after disaster recovery restore.',
            last_error_at = clock_timestamp()
        WHERE enabled IS TRUE OR delivery_enabled IS TRUE
           OR failure_state <> 'quarantined' OR next_run_at IS NOT NULL OR retry_at IS NOT NULL$sql$;
    ELSE
      EXECUTE $sql$UPDATE report_schedules
        SET enabled = false, delivery_enabled = false, next_run_at = NULL
        WHERE enabled IS TRUE OR delivery_enabled IS TRUE OR next_run_at IS NOT NULL$sql$;
    END IF;
    GET DIAGNOSTICS disabled_schedules = ROW_COUNT;
  END IF;

  IF to_regclass('public.reports') IS NOT NULL THEN
    EXECUTE 'UPDATE reports SET delivery_requested = false WHERE delivery_requested IS TRUE';
    GET DIAGNOSTICS disabled_report_deliveries = ROW_COUNT;
  END IF;

  INSERT INTO audit_logs (
    id, actor_user_id, action, resource_type, resource_id, success, metadata_json
  )
  VALUES (
    current_setting('threatlens.restore_audit_id')::uuid,
    NULL,
    'system.restore.quarantine',
    'postgresql_backup',
    current_setting('threatlens.restore_checksum'),
    true,
    jsonb_build_object(
      'schema_version', 1,
      'source', 'host-recovery-hook',
      'affected_users', affected_users,
      'revoked_api_tokens', revoked_api_tokens,
      'revoked_sessions', revoked_sessions,
      'consumed_mfa_challenges', consumed_mfa_challenges,
      'disabled_instances', disabled_instances,
      'disabled_subscriptions', disabled_subscriptions,
      'disabled_webhooks', disabled_webhooks,
      'quarantined_events', quarantined_events,
      'quarantined_deliveries', quarantined_deliveries,
      'interrupted_attempts', interrupted_attempts,
      'quarantined_legacy_deliveries', quarantined_legacy_deliveries,
      'disabled_schedules', disabled_schedules,
      'disabled_report_deliveries', disabled_report_deliveries
    )
  );
END
$quarantine$;
COMMIT;
SQL
  then
    fail "${EXIT_APPLY}" "Q503" "Transactional credential revocation and outbound quarantine failed"
  fi
  printf 'QUARANTINE_APPLY=completed\n'
}


verify_quarantine() {
  local archive_checksum
  archive_checksum="$(archive_checksum)" \
    || fail "${EXIT_VERIFY}" "Q601" "Recovery manifest validation failed"
  if ! compose exec -T --env "THREATLENS_RESTORE_CHECKSUM=${archive_checksum}" db sh -ceu '
    exec psql --no-psqlrc --set=ON_ERROR_STOP=1 --quiet \
      --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
      --set=restore_checksum="$THREATLENS_RESTORE_CHECKSUM"
  ' >/dev/null <<'SQL'
SELECT set_config('threatlens.restore_checksum', :'restore_checksum', false);
DO $verify$
BEGIN
  IF EXISTS (SELECT 1 FROM api_tokens WHERE revoked_at IS NULL) THEN
    RAISE EXCEPTION 'active API tokens remain after restore quarantine';
  END IF;
  IF to_regclass('public.auth_sessions') IS NOT NULL THEN
    IF EXISTS (SELECT 1 FROM auth_sessions WHERE revoked_at IS NULL) THEN
      RAISE EXCEPTION 'active browser sessions remain after restore quarantine';
    END IF;
  END IF;
  IF to_regclass('public.mfa_login_challenges') IS NOT NULL THEN
    IF EXISTS (SELECT 1 FROM mfa_login_challenges WHERE consumed_at IS NULL) THEN
      RAISE EXCEPTION 'active MFA login challenges remain after restore quarantine';
    END IF;
  END IF;
  IF to_regclass('public.integration_instances') IS NOT NULL THEN
    IF EXISTS (SELECT 1 FROM integration_instances WHERE enabled IS TRUE) THEN
      RAISE EXCEPTION 'enabled integration instances remain after restore quarantine';
    END IF;
  END IF;
  IF to_regclass('public.integration_subscriptions') IS NOT NULL THEN
    IF EXISTS (SELECT 1 FROM integration_subscriptions WHERE enabled IS TRUE) THEN
      RAISE EXCEPTION 'enabled integration subscriptions remain after restore quarantine';
    END IF;
  END IF;
  IF to_regclass('public.notification_webhooks') IS NOT NULL THEN
    IF EXISTS (SELECT 1 FROM notification_webhooks WHERE enabled IS TRUE) THEN
      RAISE EXCEPTION 'enabled legacy webhooks remain after restore quarantine';
    END IF;
  END IF;
  IF to_regclass('public.integration_events') IS NOT NULL THEN
    IF EXISTS (SELECT 1 FROM integration_events WHERE routing_state IN ('pending', 'routing', 'failed')) THEN
      RAISE EXCEPTION 'routable integration events remain after restore quarantine';
    END IF;
  END IF;
  IF to_regclass('public.integration_deliveries') IS NOT NULL THEN
    IF EXISTS (SELECT 1 FROM integration_deliveries WHERE state IN ('pending', 'sending', 'retry_wait')) THEN
      RAISE EXCEPTION 'sendable integration deliveries remain after restore quarantine';
    END IF;
  END IF;
  IF to_regclass('public.integration_attempts') IS NOT NULL THEN
    IF EXISTS (SELECT 1 FROM integration_attempts WHERE status = 'running') THEN
      RAISE EXCEPTION 'running integration attempts remain after restore quarantine';
    END IF;
  END IF;
  IF to_regclass('public.notification_webhook_deliveries') IS NOT NULL AND EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'notification_webhook_deliveries'
      AND column_name = 'delivery_state'
  ) THEN
    IF EXISTS (
      SELECT 1 FROM notification_webhook_deliveries
      WHERE delivery_state IN ('pending', 'sending', 'retry_wait')
    ) THEN
      RAISE EXCEPTION 'sendable legacy webhook deliveries remain after restore quarantine';
    END IF;
  END IF;
  IF to_regclass('public.report_schedules') IS NOT NULL THEN
    IF EXISTS (SELECT 1 FROM report_schedules WHERE enabled IS TRUE OR delivery_enabled IS TRUE) THEN
      RAISE EXCEPTION 'enabled report schedules remain after restore quarantine';
    END IF;
  END IF;
  IF to_regclass('public.reports') IS NOT NULL THEN
    IF EXISTS (SELECT 1 FROM reports WHERE delivery_requested IS TRUE) THEN
      RAISE EXCEPTION 'requested report deliveries remain after restore quarantine';
    END IF;
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM audit_logs
    WHERE action = 'system.restore.quarantine'
      AND resource_type = 'postgresql_backup'
      AND resource_id = current_setting('threatlens.restore_checksum')
      AND success IS TRUE
  ) THEN
    RAISE EXCEPTION 'restore quarantine audit marker is missing';
  END IF;
END
$verify$;
SQL
  then
    fail "${EXIT_VERIFY}" "Q602" "One or more restore quarantine invariants are not satisfied"
  fi
  printf 'QUARANTINE_VERIFY=passed\n'
}


main() {
  (($# == 1)) || fail "${EXIT_USAGE}" "Q203" "Exactly one phase is required"
  local phase="$1"
  initialize "${phase}"
  case "${phase}" in
    preflight) preflight ;;
    apply) apply_quarantine ;;
    verify) verify_quarantine ;;
  esac
}


main "$@"
