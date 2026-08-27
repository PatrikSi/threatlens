from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
HOOK = REPOSITORY_ROOT / "scripts" / "recovery" / "post_restore_quarantine.sh"


FAKE_DOCKER = r"""#!/usr/bin/env bash
set -eu
if [[ "$1" == "info" ]]; then
  exit 0
fi
if [[ "$1" == "inspect" ]]; then
  printf 'true\n'
  exit 0
fi
if [[ "$1" == "exec" ]]; then
  cat >"$FAKE_SQL_DIRECTORY/${THREATLENS_RECOVERY_PHASE}.sql"
  exit "${FAKE_PSQL_EXIT:-0}"
fi
if [[ "$1" != "compose" ]]; then
  exit 0
fi
shift
while (($#)); do
  case "$1" in
    --env-file|-f|--project-name)
      shift 2
      ;;
    *) break ;;
  esac
done
case "${1:-}" in
  version)
    exit 0
    ;;
  config)
    printf 'db\n'
    exit 0
    ;;
  ps)
    printf 'db\n'
    exit 0
    ;;
  exec)
    cat >"$FAKE_SQL_DIRECTORY/${THREATLENS_RECOVERY_PHASE}.sql"
    exit "${FAKE_PSQL_EXIT:-0}"
    ;;
esac
exit 0
"""


class PostRestoreQuarantineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.bin_directory = self.root / "bin"
        self.bin_directory.mkdir()
        docker = self.bin_directory / "docker"
        docker.write_text(FAKE_DOCKER, encoding="utf-8")
        docker.chmod(0o755)
        self.sql_directory = self.root / "sql"
        self.sql_directory.mkdir()
        self.env_file = self.root / ".env"
        self.env_file.write_text("POSTGRES_PASSWORD=not-used\n", encoding="utf-8")
        self.compose_file = self.root / "compose.yml"
        self.compose_file.write_text("services: {}\n", encoding="utf-8")
        self.backup = self._create_backup()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _create_backup(self) -> Path:
        backup = self.root / "backup"
        backup.mkdir()
        archive = backup / "database.dump"
        archive.write_bytes(b"PGDMP\x01quarantine-test")
        checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
        manifest = {
            "format": "threatlens-postgresql-backup",
            "schema_version": 1,
            "app_version": "1.7.0",
            "alembic_revision": "0057_system_operations",
            "postgresql_version": "16.10",
            "snapshot_time_utc": "2026-08-27T12:00:00Z",
            "metadata_collected_at_utc": "2026-08-27T12:01:00Z",
            "archive": {
                "filename": "database.dump",
                "format": "postgresql-custom",
                "size_bytes": archive.stat().st_size,
                "sha256": checksum,
            },
            "database": {
                "size_bytes": 4096,
                "count_source": "pg_stat_user_tables_estimate",
                "estimated_row_counts": {},
            },
            "encryption_key_fingerprint": None,
            "redis_included": False,
            "created_by": {"tool": "threatlens-recovery", "tool_version": "1"},
        }
        (backup / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return backup

    def _run(self, phase: str, **overrides: str) -> subprocess.CompletedProcess[str]:
        environment = dict(os.environ)
        environment.update(
            {
                "PATH": f"{self.bin_directory}:{environment['PATH']}",
                "FAKE_SQL_DIRECTORY": str(self.sql_directory),
                "THREATLENS_RECOVERY_PHASE": phase,
                "THREATLENS_RECOVERY_COMPOSE_FILES": str(self.compose_file),
                "THREATLENS_RECOVERY_ENV_FILE": str(self.env_file),
                "THREATLENS_RECOVERY_PROJECT_NAME": "threatlens-test",
                "THREATLENS_RECOVERY_MANIFEST": str(self.backup / "manifest.json"),
            }
        )
        environment.update(overrides)
        return subprocess.run(
            [str(HOOK), phase],
            capture_output=True,
            text=True,
            env=environment,
        )

    def test_preflight_is_read_only(self) -> None:
        result = self._run("preflight")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.strip().splitlines(),
            [
                "QUARANTINE_PREFLIGHT=passed",
                "QUARANTINE_DATABASE_TARGET=compose_database",
            ],
        )
        sql = (self.sql_directory / "preflight.sql").read_text(encoding="utf-8")
        self.assertNotRegex(sql, r"(?im)^\s*(UPDATE|INSERT|DELETE|ALTER|DROP)\b")
        self.assertIn("rolsuper", sql)
        self.assertIn("database.datdba", sql)

    def test_preflight_can_be_bound_to_an_isolated_database_container(self) -> None:
        result = self._run(
            "preflight",
            THREATLENS_RECOVERY_DATABASE_CONTAINER="isolated-postgres",
            THREATLENS_RECOVERY_DATABASE_USER="postgres",
            THREATLENS_RECOVERY_DATABASE_NAME="threatlens_restore_drill",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.strip().splitlines(),
            [
                "QUARANTINE_PREFLIGHT=passed",
                "QUARANTINE_DATABASE_TARGET=isolated_container",
            ],
        )

    def test_apply_is_transactional_idempotent_and_covers_outbound_state(self) -> None:
        result = self._run("apply")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "QUARANTINE_APPLY=completed")
        sql = (self.sql_directory / "apply.sql").read_text(encoding="utf-8")
        self.assertIn("BEGIN;", sql)
        self.assertIn("COMMIT;", sql)
        self.assertIn("pg_advisory_xact_lock", sql)
        self.assertIn("UPDATE users", sql)
        self.assertIn("UPDATE api_tokens", sql)
        self.assertIn("UPDATE auth_sessions", sql)
        self.assertIn("UPDATE mfa_login_challenges", sql)
        self.assertIn("UPDATE integration_instances", sql)
        self.assertIn("UPDATE integration_subscriptions", sql)
        self.assertIn("UPDATE notification_webhooks", sql)
        self.assertIn("UPDATE integration_events", sql)
        self.assertIn("UPDATE integration_deliveries", sql)
        self.assertIn("UPDATE notification_webhook_deliveries", sql)
        self.assertIn("UPDATE feeds", sql)
        self.assertIn("UPDATE ai_settings", sql)
        self.assertIn("UPDATE ai_task_runs", sql)
        self.assertIn("UPDATE ai_daily_briefs", sql)
        self.assertIn("UPDATE item_ai_enrichments", sql)
        self.assertIn("UPDATE report_schedules", sql)
        self.assertIn("UPDATE reports", sql)
        self.assertIn("UPDATE report_sections", sql)
        self.assertIn("UPDATE alert_evaluation_requests", sql)
        self.assertIn("system.restore.quarantine", sql)

    def test_verify_checks_credentials_outbound_work_and_audit_marker(self) -> None:
        result = self._run("verify")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "QUARANTINE_VERIFY=passed")
        sql = (self.sql_directory / "verify.sql").read_text(encoding="utf-8")
        for invariant in (
            "api_tokens",
            "auth_sessions",
            "mfa_login_challenges",
            "integration_instances",
            "integration_subscriptions",
            "notification_webhooks",
            "integration_events",
            "integration_deliveries",
            "notification_webhook_deliveries",
            "feeds",
            "ai_settings",
            "ai_task_runs",
            "ai_daily_briefs",
            "item_ai_enrichments",
            "report_schedules",
            "reports",
            "report_sections",
            "alert_evaluation_requests",
            "system.restore.quarantine",
        ):
            self.assertIn(invariant, sql)

    def test_apply_failure_is_explicit_and_does_not_echo_paths(self) -> None:
        result = self._run("apply", FAKE_PSQL_EXIT="1")

        self.assertEqual(result.returncode, 5)
        self.assertIn("Q503", result.stderr)
        self.assertNotIn(str(self.root), result.stdout + result.stderr)

    def test_parent_checksum_must_match_the_manifest(self) -> None:
        result = self._run(
            "preflight",
            THREATLENS_RECOVERY_ARCHIVE_SHA256="0" * 64,
        )

        self.assertEqual(result.returncode, 3)
        self.assertIn("Q313", result.stderr)
        self.assertFalse((self.sql_directory / "preflight.sql").exists())


if __name__ == "__main__":
    unittest.main()
