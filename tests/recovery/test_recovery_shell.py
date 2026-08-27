from __future__ import annotations

import fcntl
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
  if [[ " $* " == *" --format "* ]]; then
    if [[ "${*: -1}" == *postgres* ]]; then
      printf 'sha256:pinned-postgres-image\n'
    else
      printf 'sha256:pinned-backend-image\n'
    fi
  fi
  exit 0
fi
if [[ "$1" == "inspect" ]]; then
  format=""
  target="${*: -1}"
  if [[ " ${*} " == *" --format "* ]]; then
    format="$3"
  fi
  if [[ "$format" == *".State.Running"* ]]; then
    printf 'true\n'
  elif [[ "$format" == *"com.docker.compose.project"* ]]; then
    printf '%s\n' "${FAKE_PROJECT_LABEL:-threatlens-test}"
  elif [[ "$format" == *"com.docker.compose.service"* ]]; then
    if [[ "$target" == *redis* ]]; then printf 'redis\n'; else printf 'db\n'; fi
  elif [[ -n "$format" ]]; then
    count_file="${FAKE_DOCKER_LOG}.identity-count"
    identity_count=0
    [[ ! -f "$count_file" ]] || identity_count="$(cat "$count_file")"
    identity_count=$((identity_count + 1))
    printf '%s\n' "$identity_count" >"$count_file"
    dynamic_variant="${FAKE_ID_VARIANT:-}"
    if [[ -n "${FAKE_ID_CHANGE_AFTER:-}" && "$identity_count" -gt "$FAKE_ID_CHANGE_AFTER" ]]; then
      dynamic_variant="changed-after-confirmation"
    fi
    printf '%s%s|sha256:stable-image|/%s|volume:stable-data:/var/lib/data;\n' \
      "$target" "$dynamic_variant" "$target"
  else
    runtime_database_url='postgresql+psycopg://threatlens:not-logged@db:5432/threatlens'
    if [[ "${FAKE_RUNTIME_MISMATCH:-0}" == "1" ]]; then
      runtime_database_url='postgresql+psycopg://threatlens:not-logged@other:5432/threatlens'
    fi
    cat <<JSON
[
 {"Id":"fake-db-container","Config":{"Labels":{"com.docker.compose.service":"db"},"Env":["POSTGRES_DB=threatlens","POSTGRES_USER=threatlens","POSTGRES_PASSWORD=not-logged"]}},
 {"Id":"fake-redis-container","Config":{"Labels":{"com.docker.compose.service":"redis"},"Env":["REDIS_PASSWORD=not-logged"]}},
 {"Id":"fake-api-container","Config":{"Labels":{"com.docker.compose.service":"api"},"Env":["DATABASE_URL=${runtime_database_url}","REDIS_URL=redis://:not-logged@redis:6379/0","APP_DATA_ENCRYPTION_KEY=not-logged-encryption-key"]}},
 {"Id":"fake-worker-container","Config":{"Labels":{"com.docker.compose.service":"worker"},"Env":["DATABASE_URL=postgresql+psycopg://threatlens:not-logged@db:5432/threatlens","REDIS_URL=redis://:not-logged@redis:6379/0"]}},
 {"Id":"fake-worker-ai-container","Config":{"Labels":{"com.docker.compose.service":"worker-ai"},"Env":["DATABASE_URL=postgresql+psycopg://threatlens:not-logged@db:5432/threatlens","REDIS_URL=redis://:not-logged@redis:6379/0"]}},
 {"Id":"fake-worker-maintenance-container","Config":{"Labels":{"com.docker.compose.service":"worker-maintenance"},"Env":["DATABASE_URL=postgresql+psycopg://threatlens:not-logged@db:5432/threatlens","REDIS_URL=redis://:not-logged@redis:6379/0"]}},
 {"Id":"fake-worker-notifications-container","Config":{"Labels":{"com.docker.compose.service":"worker-notifications"},"Env":["DATABASE_URL=postgresql+psycopg://threatlens:not-logged@db:5432/threatlens","REDIS_URL=redis://:not-logged@redis:6379/0"]}},
 {"Id":"fake-beat-container","Config":{"Labels":{"com.docker.compose.service":"beat"},"Env":["DATABASE_URL=postgresql+psycopg://threatlens:not-logged@db:5432/threatlens","REDIS_URL=redis://:not-logged@redis:6379/0"]}}
]
JSON
  fi
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
  if [[ "${FAKE_IMAGE_TAG_MUTATED:-0}" == "1" \
    && (" $* " == *" postgres:16 "* || " $* " == *" threatlens-backend:test "*) ]]; then
    printf 'mutable image tag was used after pinning\n' >&2
    exit 97
  fi
  if [[ " $* " == *" --detach "* ]]; then
    printf 'fake-container-id\n'
    exit 0
  fi
  if [[ " $* " == *" threatlens-recovery-api-"* ]]; then
    exit "${FAKE_APP_SMOKE_EXIT:-0}"
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
  if [[ " $* " == *" sh -ceu "* ]]; then
    cat >/dev/null || true
    exit "${FAKE_PSQL_EXIT:-0}"
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
      cat <<'JSON'
{"services":{"db":{"image":"postgres:16","environment":{"POSTGRES_DB":"threatlens","POSTGRES_USER":"threatlens","POSTGRES_PASSWORD":"not-logged"},"networks":{"backplane":null}},"redis":{"image":"redis:7-alpine","environment":{"REDIS_PASSWORD":"not-logged"},"networks":{"backplane":null}},"api":{"image":"threatlens-backend:test","environment":{"DATABASE_URL":"postgresql+psycopg://threatlens:not-logged@db:5432/threatlens","REDIS_URL":"redis://:not-logged@redis:6379/0","APP_DATA_ENCRYPTION_KEY":"not-logged-encryption-key"},"networks":{"backplane":null}},"worker":{"environment":{"DATABASE_URL":"postgresql+psycopg://threatlens:not-logged@db:5432/threatlens","REDIS_URL":"redis://:not-logged@redis:6379/0"},"networks":{"backplane":null}},"worker-ai":{"environment":{"DATABASE_URL":"postgresql+psycopg://threatlens:not-logged@db:5432/threatlens","REDIS_URL":"redis://:not-logged@redis:6379/0"},"networks":{"backplane":null}},"worker-maintenance":{"environment":{"DATABASE_URL":"postgresql+psycopg://threatlens:not-logged@db:5432/threatlens","REDIS_URL":"redis://:not-logged@redis:6379/0"},"networks":{"backplane":null}},"worker-notifications":{"environment":{"DATABASE_URL":"postgresql+psycopg://threatlens:not-logged@db:5432/threatlens","REDIS_URL":"redis://:not-logged@redis:6379/0"},"networks":{"backplane":null}},"beat":{"environment":{"DATABASE_URL":"postgresql+psycopg://threatlens:not-logged@db:5432/threatlens","REDIS_URL":"redis://:not-logged@redis:6379/0"},"networks":{"backplane":null}}}}
JSON
    fi
    exit 0
    ;;
  ps)
    if [[ " ${*} " == *" --quiet "* ]]; then
      service="${*: -1}"
      printf 'fake-%s-container\n' "$service"
    else
      printf '%s\n' db redis
    fi
    exit 0
    ;;
  exec)
    if [[ " $* " == *" THREATLENS_OPERATION_ID="* ]]; then
      exit "${FAKE_LEDGER_EXIT:-0}"
    fi
    if [[ " $* " == *" --env THREATLENS_RECOVERY_PASSWORD "* ]]; then
      cat >/dev/null || true
      : >"${FAKE_DOCKER_LOG}.replacement-created"
      exit "${FAKE_CREATE_EXIT:-0}"
    fi
    if [[ " $* " == *" pg_isready "* ]]; then
      exit 0
    fi
    if [[ " $* " == *" pg_dump --version "* || " $* " == *" pg_restore --version "* ]]; then
      exit 0
    fi
    payload="$(cat || true)"
    request="$*"$'\n'"$payload"
    if [[ "$request" == *"SELECT EXISTS"* && " $* " == *"THREATLENS_DATABASE_NAME="* ]]; then
      if [[ "${FAKE_POST_DROP_PROBE_FAIL_ONCE:-0}" == "1" \
        && -e "${FAKE_DOCKER_LOG}.rollback-dropped" \
        && ! -e "${FAKE_DOCKER_LOG}.post-drop-probe-failed" ]]; then
        : >"${FAKE_DOCKER_LOG}.post-drop-probe-failed"
        exit 1
      fi
      if [[ "${FAKE_DATABASE_PROBE_EXIT:-0}" != "0" ]]; then
        exit "${FAKE_DATABASE_PROBE_EXIT}"
      fi
      if [[ " $* " == *"tl_pre_restore_"* ]]; then
        if [[ -e "${FAKE_DOCKER_LOG}.rollback-dropped" ]]; then
          printf 'f\n'
        else
          printf '%s\n' "${FAKE_ROLLBACK_DATABASE_EXISTS:-t}"
        fi
      else
        printf '%s\n' "${FAKE_TARGET_DATABASE_EXISTS:-t}"
      fi
    elif [[ "$request" == *"SELECT oid::text"* ]]; then
      if [[ " $* " == *"tl_pre_restore_"* ]]; then
        printf '100\n'
      elif [[ -e "${FAKE_DOCKER_LOG}.replacement-created" ]]; then
        printf '200\n'
      else
        printf '100\n'
      fi
    elif [[ "$request" == *"concat_ws(chr(124)"* && "$request" == *"database.oid::text"* ]]; then
      printf 'true|true|100\n'
    elif [[ " $* " == *"alembic_version"* ]]; then
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
    if [[ "$request" == *"concat_ws"* && "$request" == *"rollback_db"* ]]; then
      if [[ "$request" == *"restore_checksum"* ]]; then
        printf '%s\n' "${FAKE_FORWARD_PROOF_STATE:-1|1|1|1|1|1|1}"
      else
        printf '1|1|1|0|1\n'
      fi
    fi
    if [[ "$request" == *"REASSIGN OWNED"* && "${FAKE_FINALIZE_EXIT:-0}" != "0" ]]; then
      exit "${FAKE_FINALIZE_EXIT}"
    fi
    if [[ "$request" == *"DROP DATABASE"* ]]; then
      : >"${FAKE_DOCKER_LOG}.rollback-dropped"
      if [[ "$request" == *"target_state"* ]]; then
        rm -f "${FAKE_DOCKER_LOG}.replacement-created"
      elif [[ "${FAKE_DROP_ACK_EXIT:-0}" != "0" ]]; then
        exit "${FAKE_DROP_ACK_EXIT}"
      fi
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

    def _run(
        self, *arguments: str, **environment: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                str(RECOVERY),
                "--compose-file",
                str(self.compose_file),
                "--env-file",
                str(self.env_file),
                "--project-name",
                "threatlens-test",
                "--journal-dir",
                str(self.root / "journal"),
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
            "encryption_key_fingerprint": "sha256:"
            + hashlib.sha256(b"not-logged-encryption-key").hexdigest()[:32],
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
            'if [[ "$1" == "preflight" ]]; then\n'
            "  printf 'QUARANTINE_PREFLIGHT=passed\\nQUARANTINE_DATABASE_TARGET=%s\\n' "
            '"$([[ -n "${THREATLENS_RECOVERY_DATABASE_CONTAINER:-}" ]] '
            '&& echo isolated_container || echo compose_database)"\n'
            "fi\n"
            'if [[ "$1" == "apply" && "${FAKE_HOOK_KILL_PARENT:-0}" == "1" ]]; then\n'
            '  kill -KILL "$PPID"\n'
            "fi\n"
            '[[ "$1" != "${FAKE_HOOK_FAIL_PHASE:-}" ]]\n',
            encoding="utf-8",
        )
        hook.chmod(0o755)
        return hook, hook_log

    def _restore_confirmation(self, backup: Path) -> str:
        result = self._run(
            "restore",
            "--backup",
            str(backup),
            "--show-confirmation",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        confirmation = result.stdout.strip()
        self.assertIn("project=threatlens-test", confirmation)
        self.assertIn("database=threatlens", confirmation)
        self.assertRegex(confirmation, r"archive=[0-9a-f]{64}")
        self.assertRegex(confirmation, r"deployment=[0-9a-f]{64}")
        return confirmation

    def _ledger_lines(self) -> list[str]:
        return [
            line
            for line in self.docker_log.read_text(encoding="utf-8").splitlines()
            if "THREATLENS_OPERATION_ID=" in line
            and "THREATLENS_OPERATION_STATUS=running" not in line
        ]

    def _ledger_start_lines(self) -> list[str]:
        return [
            line
            for line in self.docker_log.read_text(encoding="utf-8").splitlines()
            if "THREATLENS_OPERATION_ID=" in line
            and "THREATLENS_OPERATION_STATUS=running" in line
        ]

    def _assert_started_then_finished(self, terminal_line: str) -> None:
        start_lines = self._ledger_start_lines()
        self.assertEqual(len(start_lines), 1)
        start_id = start_lines[0].split("THREATLENS_OPERATION_ID=", 1)[1].split()[0]
        terminal_id = terminal_line.split("THREATLENS_OPERATION_ID=", 1)[1].split()[0]
        self.assertEqual(start_id, terminal_id)
        self.assertIn("ledger_scope_id", start_lines[0])

    def test_restore_refuses_wrong_confirmation_before_mutation(self) -> None:
        backup = self._create_backup()
        result = self._run(
            "restore",
            "--backup",
            str(backup),
            "--confirm",
            "restore it",
            "--acknowledge-data-loss",
        )

        self.assertEqual(result.returncode, 7)
        self.assertIn("E704", result.stderr)
        log = self.docker_log.read_text(encoding="utf-8")
        self.assertNotIn(" compose stop ", f" {log} ")

    def test_restore_refuses_concurrent_reconciliation_lock(self) -> None:
        journal_root = self.root / "journal"
        journal_root.mkdir(mode=0o700)
        lock_path = journal_root / "operation.lock"
        with lock_path.open("w", encoding="utf-8") as lock_file:
            lock_path.chmod(0o600)
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

            result = self._run("reconcile")

        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertIn("E723", result.stderr)
        self.assertIn("Another recovery operation", result.stderr)

    def test_restore_requires_explicit_encryption_fingerprint_mismatch_acknowledgement(
        self,
    ) -> None:
        backup = self._create_backup()
        manifest_path = backup / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["encryption_key_fingerprint"] = "sha256:" + "0" * 32
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        refused = self._run(
            "restore",
            "--backup",
            str(backup),
            "--show-confirmation",
        )
        acknowledged = self._run(
            "restore",
            "--backup",
            str(backup),
            "--show-confirmation",
            "--acknowledge-encryption-key-mismatch",
        )

        self.assertEqual(refused.returncode, 7, refused.stderr)
        self.assertIn("E709", refused.stderr)
        self.assertEqual(acknowledged.returncode, 0, acknowledged.stderr)
        self.assertIn("explicitly acknowledged", acknowledged.stderr)

    def test_restore_refuses_unsafe_quarantine_hook_before_mutation(self) -> None:
        backup = self._create_backup()
        confirmation = self._restore_confirmation(backup)
        result = self._run(
            "restore",
            "--backup",
            str(backup),
            "--confirm",
            confirmation,
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
        self.assertIn(
            "THREATLENS_OPERATION_TYPE=restore_drill", self._ledger_lines()[0]
        )
        self.assertIn("THREATLENS_OPERATION_STATUS=failed", self._ledger_lines()[0])
        self.assertIn("THREATLENS_OPERATION_ERROR_CODE=E605", self._ledger_lines()[0])
        self._assert_started_then_finished(self._ledger_lines()[0])

    def test_failed_packaged_code_smoke_cleans_all_isolated_resources(self) -> None:
        backup = self._create_backup()

        result = self._run(
            "drill",
            "--backup",
            str(backup),
            FAKE_APP_SMOKE_EXIT="1",
        )

        self.assertEqual(result.returncode, 6, result.stderr)
        self.assertIn("E615", result.stderr)
        log = self.docker_log.read_text(encoding="utf-8")
        self.assertIn("rm --force threatlens-recovery-api-", log)
        self.assertIn("rm --force threatlens-recovery-pg-", log)
        self.assertIn("volume rm --force threatlens-recovery-db-", log)
        self.assertIn("network rm threatlens-recovery-net-", log)

    def test_drill_runs_packaged_code_before_exact_hook_preflight(self) -> None:
        backup = self._create_backup()
        hook_log = self.root / "ordered-hook.log"
        hook = self.root / "ordered-hook.sh"
        hook.write_text(
            "#!/usr/bin/env bash\n"
            "set -eu\n"
            'grep -q "run --name threatlens-recovery-api-" "$FAKE_DOCKER_LOG"\n'
            'printf \'%s\\n\' "$1" >>"$FAKE_HOOK_LOG"\n'
            "printf 'QUARANTINE_PREFLIGHT=passed\\nQUARANTINE_DATABASE_TARGET=isolated_container\\n'\n",
            encoding="utf-8",
        )
        hook.chmod(0o755)

        result = self._run(
            "drill",
            "--backup",
            str(backup),
            "--quarantine-hook",
            str(hook),
            FAKE_HOOK_LOG=str(hook_log),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            hook_log.read_text(encoding="utf-8").splitlines(), ["preflight"]
        )

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

    def test_backup_lock_does_not_follow_or_truncate_a_symlink(self) -> None:
        output_directory = self.root / "backups"
        output_directory.mkdir()
        protected = self.root / "protected.txt"
        protected.write_text("must remain intact", encoding="utf-8")
        (output_directory / ".threatlens-recovery.lock").symlink_to(protected)

        result = self._run("backup", "--output-dir", str(output_directory))

        self.assertEqual(result.returncode, 5, result.stderr)
        self.assertIn("E504", result.stderr)
        self.assertEqual(protected.read_text(encoding="utf-8"), "must remain intact")

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
        self._assert_started_then_finished(ledger_lines[0])

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
        self._assert_started_then_finished(ledger_line)

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
        self._assert_started_then_finished(ledger_lines[0])

    def test_restore_records_success_only_after_hook_verifies(self) -> None:
        backup = self._create_backup()
        hook, hook_log = self._create_hook()
        confirmation = self._restore_confirmation(backup)

        result = self._run(
            "restore",
            "--backup",
            str(backup),
            "--confirm",
            confirmation,
            "--acknowledge-data-loss",
            "--quarantine-hook",
            str(hook),
            "--safety-backup-dir",
            str(self.root / "safety"),
            FAKE_HOOK_LOG=str(hook_log),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            hook_log.read_text(encoding="utf-8").splitlines(),
            ["preflight", "preflight", "apply", "verify"],
        )
        ledger_lines = self._ledger_lines()
        self.assertEqual(len(ledger_lines), 1)
        self.assertIn("THREATLENS_OPERATION_TYPE=restore", ledger_lines[0])
        self.assertIn("THREATLENS_OPERATION_STATUS=succeeded", ledger_lines[0])
        self.assertIn("outbound_quarantined", ledger_lines[0])
        self.assertNotIn(str(backup), ledger_lines[0])
        command_log = self.docker_log.read_text(encoding="utf-8")
        self.assertNotIn("THREATLENS_RECOVERY_PASSWORD=", command_log)
        self.assertNotIn("not-logged", command_log)

    def test_restore_confirmation_expires_when_live_target_identity_changes(
        self,
    ) -> None:
        backup = self._create_backup()
        confirmation = self._restore_confirmation(backup)

        result = self._run(
            "restore",
            "--backup",
            str(backup),
            "--confirm",
            confirmation,
            "--acknowledge-data-loss",
            FAKE_ID_VARIANT="replacement",
        )

        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertIn("E704", result.stderr)
        self.assertNotIn(
            " compose stop ", f" {self.docker_log.read_text(encoding='utf-8')} "
        )

    def test_restore_revalidates_live_identity_immediately_before_fencing(self) -> None:
        backup = self._create_backup()
        confirmation = self._restore_confirmation(backup)

        result = self._run(
            "restore",
            "--backup",
            str(backup),
            "--confirm",
            confirmation,
            "--acknowledge-data-loss",
            "--safety-backup-dir",
            str(self.root / "safety"),
            FAKE_ID_CHANGE_AFTER="4",
        )

        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertIn("E717", result.stderr)
        self.assertNotIn(
            "THREATLENS_RECOVERY_PASSWORD",
            self.docker_log.read_text(encoding="utf-8"),
        )

    def test_restore_refuses_running_container_environment_drift(self) -> None:
        backup = self._create_backup()

        result = self._run(
            "restore",
            "--backup",
            str(backup),
            "--show-confirmation",
            FAKE_RUNTIME_MISMATCH="1",
        )

        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertIn("E715", result.stderr)

    def test_image_tag_mutation_cannot_change_pinned_restore_images(self) -> None:
        backup = self._create_backup()
        hook, hook_log = self._create_hook()
        confirmation = self._restore_confirmation(backup)

        result = self._run(
            "restore",
            "--backup",
            str(backup),
            "--confirm",
            confirmation,
            "--acknowledge-data-loss",
            "--quarantine-hook",
            str(hook),
            "--safety-backup-dir",
            str(self.root / "safety"),
            FAKE_HOOK_LOG=str(hook_log),
            FAKE_IMAGE_TAG_MUTATED="1",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        command_log = self.docker_log.read_text(encoding="utf-8")
        self.assertIn("sha256:pinned-postgres-image", command_log)
        self.assertIn("sha256:pinned-backend-image", command_log)
        run_lines = [
            line for line in command_log.splitlines() if line.startswith("run ")
        ]
        self.assertTrue(run_lines)
        self.assertFalse(any(" postgres:16 " in f" {line} " for line in run_lines))
        self.assertFalse(
            any(" threatlens-backend:test " in f" {line} " for line in run_lines)
        )

    def test_lost_drop_ack_is_reconciled_as_forward_commit_not_rollback(self) -> None:
        backup = self._create_backup()
        hook, hook_log = self._create_hook()
        confirmation = self._restore_confirmation(backup)

        result = self._run(
            "restore",
            "--backup",
            str(backup),
            "--confirm",
            confirmation,
            "--acknowledge-data-loss",
            "--quarantine-hook",
            str(hook),
            "--safety-backup-dir",
            str(self.root / "safety"),
            FAKE_HOOK_LOG=str(hook_log),
            FAKE_DROP_ACK_EXIT="1",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "RESTORE_STATUS=completed_quarantined_after_reconciliation", result.stdout
        )
        self.assertIn("Forward-commit reconciliation proved", result.stderr)
        self.assertNotIn("restored the original database identity", result.stderr)

    def test_post_drop_probe_failure_is_reconciled_as_forward_commit(self) -> None:
        backup = self._create_backup()
        hook, hook_log = self._create_hook()
        confirmation = self._restore_confirmation(backup)

        result = self._run(
            "restore",
            "--backup",
            str(backup),
            "--confirm",
            confirmation,
            "--acknowledge-data-loss",
            "--quarantine-hook",
            str(hook),
            "--safety-backup-dir",
            str(self.root / "safety"),
            FAKE_HOOK_LOG=str(hook_log),
            FAKE_POST_DROP_PROBE_FAIL_ONCE="1",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("completed_quarantined_after_reconciliation", result.stdout)

    def test_forward_commit_refuses_mismatched_quarantine_marker(self) -> None:
        backup = self._create_backup()
        hook, hook_log = self._create_hook()
        confirmation = self._restore_confirmation(backup)

        result = self._run(
            "restore",
            "--backup",
            str(backup),
            "--confirm",
            confirmation,
            "--acknowledge-data-loss",
            "--quarantine-hook",
            str(hook),
            "--safety-backup-dir",
            str(self.root / "safety"),
            FAKE_HOOK_LOG=str(hook_log),
            FAKE_DROP_ACK_EXIT="1",
            FAKE_FORWARD_PROOF_STATE="1|1|1|1|1|1|0",
        )

        self.assertEqual(result.returncode, 8, result.stderr)
        self.assertIn("CRITICAL", result.stderr)
        self.assertNotIn("completed_quarantined_after_reconciliation", result.stdout)
        self.assertEqual(self._ledger_lines(), [])
        journal = self.root / "journal" / "active" / "journal.json"
        self.assertEqual(
            json.loads(journal.read_text(encoding="utf-8"))["status"], "unknown"
        )

    def test_interrupted_journal_is_reconciled_and_ingested(self) -> None:
        backup = self._create_backup()
        hook, hook_log = self._create_hook()
        confirmation = self._restore_confirmation(backup)

        interrupted = self._run(
            "restore",
            "--backup",
            str(backup),
            "--confirm",
            confirmation,
            "--acknowledge-data-loss",
            "--quarantine-hook",
            str(hook),
            "--safety-backup-dir",
            str(self.root / "safety"),
            FAKE_HOOK_LOG=str(hook_log),
            FAKE_HOOK_KILL_PARENT="1",
        )

        self.assertNotEqual(interrupted.returncode, 0)
        journal = self.root / "journal" / "active" / "journal.json"
        self.assertTrue(journal.is_file())
        self.assertEqual(
            json.loads(journal.read_text(encoding="utf-8"))["status"], "running"
        )

        reconciled = self._run("reconcile")

        self.assertEqual(reconciled.returncode, 0, reconciled.stderr)
        self.assertIn("RECONCILE_STATUS=rolled_back", reconciled.stdout)
        self.assertFalse((self.root / "journal" / "active").exists())
        history = list((self.root / "journal" / "history").glob("*.json"))
        self.assertEqual(len(history), 1)
        self.assertIn("THREATLENS_OPERATION_STATUS=failed", self._ledger_lines()[-1])

    def test_terminal_archive_death_is_reconciled_from_durable_receipt(self) -> None:
        backup = self._create_backup()
        hook, hook_log = self._create_hook()
        confirmation = self._restore_confirmation(backup)

        interrupted = self._run(
            "restore",
            "--backup",
            str(backup),
            "--confirm",
            confirmation,
            "--acknowledge-data-loss",
            "--quarantine-hook",
            str(hook),
            "--safety-backup-dir",
            str(self.root / "safety"),
            FAKE_HOOK_LOG=str(hook_log),
            THREATLENS_RECOVERY_JOURNAL_TEST_FAILPOINT="archive_after_pending_rmdir",
        )

        self.assertNotEqual(interrupted.returncode, 0)
        self.assertFalse((self.root / "journal" / "active").exists())
        self.assertTrue((self.root / "journal" / "last-archived.json").is_file())

        reconciled = self._run("reconcile")

        self.assertEqual(reconciled.returncode, 0, reconciled.stderr)
        self.assertIn("RECONCILE_STATUS=evidence_ingested", reconciled.stdout)
        self.assertFalse((self.root / "journal" / "active").exists())
        self.assertEqual(len(list((self.root / "journal" / "history").glob("*.json"))), 1)

    def test_restore_refuses_mismatched_container_project_identity(self) -> None:
        backup = self._create_backup()

        result = self._run(
            "restore",
            "--backup",
            str(backup),
            "--show-confirmation",
            FAKE_PROJECT_LABEL="another-project",
        )

        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertIn("E712", result.stderr)

    def test_failed_restore_records_only_after_rollback_reconciliation(self) -> None:
        backup = self._create_backup()
        hook, hook_log = self._create_hook()
        confirmation = self._restore_confirmation(backup)

        result = self._run(
            "restore",
            "--backup",
            str(backup),
            "--confirm",
            confirmation,
            "--acknowledge-data-loss",
            "--quarantine-hook",
            str(hook),
            "--safety-backup-dir",
            str(self.root / "safety"),
            FAKE_HOOK_LOG=str(hook_log),
            FAKE_HOOK_FAIL_PHASE="verify",
        )

        self.assertEqual(result.returncode, 8, result.stderr)
        self.assertIn("E809", result.stderr)
        self.assertEqual(
            hook_log.read_text(encoding="utf-8").splitlines(),
            ["preflight", "preflight", "apply", "verify"],
        )
        self.assertEqual(len(self._ledger_lines()), 1)
        self.assertIn("THREATLENS_OPERATION_STATUS=failed", self._ledger_lines()[0])

    def test_interruption_after_rename_recovers_when_clean_target_is_missing(
        self,
    ) -> None:
        backup = self._create_backup()
        hook, hook_log = self._create_hook()
        confirmation = self._restore_confirmation(backup)

        result = self._run(
            "restore",
            "--backup",
            str(backup),
            "--confirm",
            confirmation,
            "--acknowledge-data-loss",
            "--quarantine-hook",
            str(hook),
            "--safety-backup-dir",
            str(self.root / "safety"),
            FAKE_HOOK_LOG=str(hook_log),
            FAKE_CREATE_EXIT="130",
            FAKE_ROLLBACK_DATABASE_EXISTS="t",
            FAKE_TARGET_DATABASE_EXISTS="f",
        )

        self.assertEqual(result.returncode, 8, result.stderr)
        self.assertIn("E805", result.stderr)
        self.assertIn(
            "Rollback reconciliation restored the original database identity",
            result.stderr,
        )
        self.assertNotIn("CRITICAL", result.stderr)
        command_log = self.docker_log.read_text(encoding="utf-8")
        self.assertIn("THREATLENS_TARGET_STATE=missing", command_log)
        self.assertEqual(len(self._ledger_lines()), 1)
        self.assertIn("THREATLENS_OPERATION_STATUS=failed", self._ledger_lines()[0])

    def test_probe_failure_is_not_treated_as_a_missing_database(self) -> None:
        backup = self._create_backup()
        hook, hook_log = self._create_hook()
        confirmation = self._restore_confirmation(backup)

        result = self._run(
            "restore",
            "--backup",
            str(backup),
            "--confirm",
            confirmation,
            "--acknowledge-data-loss",
            "--quarantine-hook",
            str(hook),
            "--safety-backup-dir",
            str(self.root / "safety"),
            FAKE_HOOK_LOG=str(hook_log),
            FAKE_HOOK_FAIL_PHASE="verify",
            FAKE_DATABASE_PROBE_EXIT="1",
        )

        self.assertEqual(result.returncode, 8, result.stderr)
        self.assertIn("Database existence probe failed", result.stderr)
        self.assertIn("CRITICAL", result.stderr)
        self.assertEqual(self._ledger_lines(), [])

    def test_finalization_failure_cannot_be_reported_as_restore_success(self) -> None:
        backup = self._create_backup()
        hook, hook_log = self._create_hook()
        confirmation = self._restore_confirmation(backup)

        result = self._run(
            "restore",
            "--backup",
            str(backup),
            "--confirm",
            confirmation,
            "--acknowledge-data-loss",
            "--quarantine-hook",
            str(hook),
            "--safety-backup-dir",
            str(self.root / "safety"),
            FAKE_HOOK_LOG=str(hook_log),
            FAKE_FINALIZE_EXIT="1",
        )

        self.assertEqual(result.returncode, 8, result.stderr)
        self.assertIn("E812", result.stderr)
        self.assertNotIn("RESTORE_STATUS=completed", result.stdout)
        self.assertIn(
            "Rollback reconciliation restored the original database identity",
            result.stderr,
        )
        self.assertEqual(len(self._ledger_lines()), 1)
        self.assertIn("THREATLENS_OPERATION_STATUS=failed", self._ledger_lines()[0])

    def test_restore_history_failure_does_not_change_success(self) -> None:
        backup = self._create_backup()
        hook, hook_log = self._create_hook()
        confirmation = self._restore_confirmation(backup)

        result = self._run(
            "restore",
            "--backup",
            str(backup),
            "--confirm",
            confirmation,
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
        self._assert_started_then_finished(ledger_lines[0])


if __name__ == "__main__":
    unittest.main()
