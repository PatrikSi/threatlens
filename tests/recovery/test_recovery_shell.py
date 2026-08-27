from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RECOVERY = REPOSITORY_ROOT / "scripts" / "recovery" / "threatlens-recovery.sh"


FAKE_DOCKER = r"""#!/usr/bin/env bash
set -eu
printf '%s\n' "$*" >>"$FAKE_DOCKER_LOG"

if [[ "$1" == "info" ]]; then
  exit 0
fi
if [[ "$1" == "image" && "$2" == "inspect" ]]; then
  exit 0
fi
if [[ "$1" == "inspect" ]]; then
  printf 'true\n'
  exit 0
fi
if [[ "$1" == "rm" ]]; then
  exit 0
fi
if [[ "$1" == "network" ]]; then
  exit "${FAKE_NETWORK_EXIT:-0}"
fi
if [[ "$1" == "volume" ]]; then
  exit "${FAKE_VOLUME_EXIT:-0}"
fi
if [[ "$1" == "run" ]]; then
  if [[ " $* " == *" --detach "* ]]; then
    printf 'fake-container-id\n'
    exit 0
  fi
  if [[ " $* " == *" --list "* ]]; then
    cat >/dev/null
  fi
  exit 0
fi
if [[ "$1" == "exec" ]]; then
  if [[ " $* " == *" pg_isready "* ]]; then
    exit 0
  fi
  if [[ " $* " == *" pg_restore "* ]]; then
    cat >/dev/null
    exit "${FAKE_DRILL_RESTORE_EXIT:-0}"
  fi
  if [[ " $* " == *"alembic_version"* ]]; then
    printf '0056_report_task_lineage\n'
  elif [[ " $* " == *"pg_catalog.pg_class"* ]]; then
    printf '42\n'
  elif [[ " $* " == *"NOT convalidated"* ]]; then
    printf '0\n'
  elif [[ " $* " == *"SELECT 1 FROM users"* ]]; then
    printf '1\n'
  fi
  exit 0
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
    if [[ " ${*} " == *" --services "* ]]; then
      printf '%s\n' db redis api worker worker-ai worker-maintenance worker-notifications beat web
    else
      printf '{"services":{"db":{"image":"postgres:16"}}}\n'
    fi
    exit 0
    ;;
  ps)
    printf '%s\n' db redis
    exit 0
    ;;
  exec)
    if [[ " $* " == *" THREATLENS_OPERATION_ID="* ]]; then
      exit "${FAKE_LEDGER_EXIT:-0}"
    fi
    if [[ " $* " == *" pg_isready "* ]]; then
      exit 0
    fi
    if [[ " $* " == *" pg_dump --version "* || " $* " == *" pg_restore --version "* ]]; then
      exit 0
    fi
    if [[ " $* " == *"alembic_version"* ]]; then
      printf '0056_report_task_lineage\n'
    elif [[ " $* " == *"SHOW server_version"* ]]; then
      printf '16.10\n'
    elif [[ " $* " == *"clock_timestamp"* ]]; then
      printf '2026-08-27T12:00:00Z\n'
    elif [[ " $* " == *"pg_database_size"* ]]; then
      printf '4096\n'
    elif [[ " $* " == *"pg_stat_user_tables"* ]]; then
      printf '{}\n'
    elif [[ " $* " == *"pg_dump --username"* ]]; then
      printf 'partial-dump'
      exit "${FAKE_BACKUP_EXIT:-0}"
    fi
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
"""


class RecoveryShellTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.bin_directory = self.root / "bin"
        self.bin_directory.mkdir()
        docker = self.bin_directory / "docker"
        docker.write_text(FAKE_DOCKER, encoding="utf-8")
        docker.chmod(0o755)
        self.docker_log = self.root / "docker.log"
        self.docker_log.touch()
        self.env_file = self.root / ".env"
        self.env_file.write_text(
            "POSTGRES_PASSWORD=not-logged\n"
            "REDIS_PASSWORD=not-logged\n"
            "APP_DATA_ENCRYPTION_KEY=not-logged-encryption-key\n",
            encoding="utf-8",
        )
        self.compose_file = self.root / "compose.yml"
        self.compose_file.write_text("services: {}\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _environment(self, **overrides: str) -> dict[str, str]:
        environment = dict(os.environ)
        environment.update(
            {
                "PATH": f"{self.bin_directory}:{environment['PATH']}",
                "FAKE_DOCKER_LOG": str(self.docker_log),
            }
        )
        environment.update(overrides)
        return environment

    def _run(self, *arguments: str, **environment: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                str(RECOVERY),
                "--compose-file",
                str(self.compose_file),
                "--env-file",
                str(self.env_file),
                *arguments,
            ],
            capture_output=True,
            text=True,
            env=self._environment(**environment),
        )

    def _create_backup(self) -> Path:
        backup = self.root / "threatlens-postgresql-test"
        backup.mkdir()
        archive = backup / "database.dump"
        archive.write_bytes(b"PGDMP\x01fake-archive")
        checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
        manifest = {
            "format": "threatlens-postgresql-backup",
            "schema_version": 1,
            "app_version": "1.7.0",
            "alembic_revision": "0056_report_task_lineage",
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

    def _create_hook(self) -> tuple[Path, Path]:
        hook_log = self.root / "hook.log"
        hook = self.root / "quarantine-hook.sh"
        hook.write_text(
            "#!/usr/bin/env bash\n"
            "set -eu\n"
            'printf \'%s\\n\' "$1" >>"$FAKE_HOOK_LOG"\n'
            '[[ "$1" != "${FAKE_HOOK_FAIL_PHASE:-}" ]]\n',
            encoding="utf-8",
        )
        hook.chmod(0o755)
        return hook, hook_log

    def _ledger_lines(self) -> list[str]:
        return [
            line
            for line in self.docker_log.read_text(encoding="utf-8").splitlines()
            if "THREATLENS_OPERATION_ID=" in line
        ]

    def test_restore_refuses_wrong_confirmation_before_mutation(self) -> None:
        result = self._run(
            "restore",
            "--backup",
            str(self.root / "missing"),
            "--confirm",
            "restore it",
            "--acknowledge-data-loss",
        )

        self.assertEqual(result.returncode, 7)
        self.assertIn("E704", result.stderr)
        log = self.docker_log.read_text(encoding="utf-8")
        self.assertNotIn(" compose stop ", f" {log} ")

    def test_restore_refuses_unsafe_quarantine_hook_before_mutation(self) -> None:
        result = self._run(
            "restore",
            "--backup",
            str(self.root / "missing"),
            "--confirm",
            "RESTORE THREATLENS POSTGRESQL",
            "--acknowledge-data-loss",
            "--quarantine-hook",
            str(self.root / "missing-hook"),
        )

        self.assertEqual(result.returncode, 7)
        self.assertIn("E702", result.stderr)
        log = self.docker_log.read_text(encoding="utf-8")
        self.assertNotIn(" compose stop ", f" {log} ")

    def test_failed_drill_restore_cleans_all_isolated_resources(self) -> None:
        backup = self._create_backup()

        result = self._run(
            "drill",
            "--backup",
            str(backup),
            FAKE_DRILL_RESTORE_EXIT="1",
        )

        self.assertEqual(result.returncode, 6, result.stderr)
        self.assertIn("E605", result.stderr)
        log = self.docker_log.read_text(encoding="utf-8")
        self.assertIn("rm --force threatlens-recovery-pg-", log)
        self.assertIn("volume rm --force threatlens-recovery-db-", log)
        self.assertIn("network rm threatlens-recovery-net-", log)
        self.assertEqual(len(self._ledger_lines()), 1)
        self.assertIn("THREATLENS_OPERATION_TYPE=restore_drill", self._ledger_lines()[0])
        self.assertIn("THREATLENS_OPERATION_STATUS=failed", self._ledger_lines()[0])
        self.assertIn("THREATLENS_OPERATION_ERROR_CODE=E605", self._ledger_lines()[0])

    def test_interrupted_backup_removes_partial_directory(self) -> None:
        output_directory = self.root / "backups"

        result = self._run(
            "backup",
            "--output-dir",
            str(output_directory),
            FAKE_BACKUP_EXIT="1",
        )

        self.assertEqual(result.returncode, 5, result.stderr)
        self.assertIn("E512", result.stderr)
        self.assertFalse(list(output_directory.glob(".threatlens-backup.partial.*")))
        self.assertFalse(list(output_directory.glob("threatlens-postgresql-*")))

    def test_command_lines_do_not_contain_configured_secrets(self) -> None:
        result = self._run(
            "backup",
            "--output-dir",
            str(self.root / "backups"),
            FAKE_BACKUP_EXIT="1",
        )
        self.assertEqual(result.returncode, 5)
        command_log = self.docker_log.read_text(encoding="utf-8")
        self.assertNotIn("not-logged", command_log)

    def test_successful_backup_records_sanitized_history(self) -> None:
        output_directory = self.root / "backups"

        result = self._run("backup", "--output-dir", str(output_directory))

        self.assertEqual(result.returncode, 0, result.stderr)
        ledger_lines = self._ledger_lines()
        self.assertEqual(len(ledger_lines), 1)
        self.assertIn("THREATLENS_OPERATION_TYPE=backup", ledger_lines[0])
        self.assertIn("THREATLENS_OPERATION_STATUS=succeeded", ledger_lines[0])
        self.assertIn("archive_sha256", ledger_lines[0])
        self.assertNotIn(str(output_directory), ledger_lines[0])

    def test_successful_verify_records_sanitized_history(self) -> None:
        backup = self._create_backup()

        result = self._run("verify", "--backup", str(backup))

        self.assertEqual(result.returncode, 0, result.stderr)
        ledger_lines = self._ledger_lines()
        self.assertEqual(len(ledger_lines), 1)
        ledger_line = ledger_lines[0]
        self.assertIn("THREATLENS_OPERATION_TYPE=verify", ledger_line)
        self.assertIn("THREATLENS_OPERATION_STATUS=succeeded", ledger_line)
        self.assertIn("archive_sha256", ledger_line)
        self.assertNotIn(str(backup), ledger_line)

    def test_history_failure_does_not_change_successful_verify(self) -> None:
        backup = self._create_backup()

        result = self._run(
            "verify",
            "--backup",
            str(backup),
            FAKE_LEDGER_EXIT="42",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("history is unavailable", result.stderr)

    def test_successful_drill_records_sanitized_history(self) -> None:
        backup = self._create_backup()

        result = self._run("drill", "--backup", str(backup))

        self.assertEqual(result.returncode, 0, result.stderr)
        ledger_lines = self._ledger_lines()
        self.assertEqual(len(ledger_lines), 1)
        self.assertIn("THREATLENS_OPERATION_TYPE=restore_drill", ledger_lines[0])
        self.assertIn("THREATLENS_OPERATION_STATUS=succeeded", ledger_lines[0])
        self.assertIn("table_count", ledger_lines[0])
        self.assertNotIn(str(backup), ledger_lines[0])

    def test_restore_records_success_only_after_hook_verifies(self) -> None:
        backup = self._create_backup()
        hook, hook_log = self._create_hook()

        result = self._run(
            "restore",
            "--backup",
            str(backup),
            "--confirm",
            "RESTORE THREATLENS POSTGRESQL",
            "--acknowledge-data-loss",
            "--quarantine-hook",
            str(hook),
            "--safety-backup-dir",
            str(self.root / "safety"),
            FAKE_HOOK_LOG=str(hook_log),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(hook_log.read_text(encoding="utf-8").splitlines(), ["preflight", "apply", "verify"])
        ledger_lines = self._ledger_lines()
        self.assertEqual(len(ledger_lines), 1)
        self.assertIn("THREATLENS_OPERATION_TYPE=restore", ledger_lines[0])
        self.assertIn("THREATLENS_OPERATION_STATUS=succeeded", ledger_lines[0])
        self.assertIn("outbound_quarantined", ledger_lines[0])
        self.assertNotIn(str(backup), ledger_lines[0])

    def test_restore_does_not_record_before_hook_verification(self) -> None:
        backup = self._create_backup()
        hook, hook_log = self._create_hook()

        result = self._run(
            "restore",
            "--backup",
            str(backup),
            "--confirm",
            "RESTORE THREATLENS POSTGRESQL",
            "--acknowledge-data-loss",
            "--quarantine-hook",
            str(hook),
            "--safety-backup-dir",
            str(self.root / "safety"),
            FAKE_HOOK_LOG=str(hook_log),
            FAKE_HOOK_FAIL_PHASE="verify",
        )

        self.assertEqual(result.returncode, 8, result.stderr)
        self.assertIn("E808", result.stderr)
        self.assertEqual(hook_log.read_text(encoding="utf-8").splitlines(), ["preflight", "apply", "verify"])
        self.assertEqual(self._ledger_lines(), [])

    def test_restore_history_failure_does_not_change_success(self) -> None:
        backup = self._create_backup()
        hook, hook_log = self._create_hook()

        result = self._run(
            "restore",
            "--backup",
            str(backup),
            "--confirm",
            "RESTORE THREATLENS POSTGRESQL",
            "--acknowledge-data-loss",
            "--quarantine-hook",
            str(hook),
            "--safety-backup-dir",
            str(self.root / "safety"),
            FAKE_HOOK_LOG=str(hook_log),
            FAKE_LEDGER_EXIT="42",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("history is unavailable", result.stderr)

    def test_failed_backup_records_failed_history_when_available(self) -> None:
        result = self._run(
            "backup",
            "--output-dir",
            str(self.root / "backups"),
            FAKE_BACKUP_EXIT="1",
        )

        self.assertEqual(result.returncode, 5)
        ledger_lines = self._ledger_lines()
        self.assertEqual(len(ledger_lines), 1)
        self.assertIn("THREATLENS_OPERATION_TYPE=backup", ledger_lines[0])
        self.assertIn("THREATLENS_OPERATION_STATUS=failed", ledger_lines[0])
        self.assertIn("THREATLENS_OPERATION_ERROR_CODE=E512", ledger_lines[0])


if __name__ == "__main__":
    unittest.main()
