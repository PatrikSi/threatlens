from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RECOVERY = REPOSITORY_ROOT / "scripts" / "recovery" / "threatlens-recovery.sh"
COMPOSE_FILE = REPOSITORY_ROOT / "tests" / "recovery" / "docker-compose.e2e.yml"
PROJECT = "threatlens-recovery-e2e"


@unittest.skipUnless(
    os.environ.get("THREATLENS_RUN_DOCKER_RECOVERY_E2E") == "1",
    "set THREATLENS_RUN_DOCKER_RECOVERY_E2E=1 to run destructive disposable-container tests",
)
class RecoveryDockerEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        if shutil.which("docker") is None:
            self.skipTest("Docker is unavailable")
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.env_file = self.root / "recovery.env"
        self.backup_directory = self.root / "backups"
        self.environment = dict(os.environ)
        encryption_key = secrets.token_urlsafe(48)
        values = {
            "APP_DATA_ENCRYPTION_KEY": encryption_key,
            "RECOVERY_E2E_ADMIN_PASSWORD": secrets.token_hex(24),
            "RECOVERY_E2E_ENCRYPTION_KEY": encryption_key,
            "RECOVERY_E2E_JWT_SECRET": secrets.token_urlsafe(48),
            "RECOVERY_E2E_POSTGRES_PASSWORD": secrets.token_hex(24),
            "RECOVERY_E2E_REDIS_PASSWORD": secrets.token_hex(24),
        }
        self.environment.update(values)
        self.env_file.write_text(
            "".join(f"{key}={value}\n" for key, value in sorted(values.items())),
            encoding="utf-8",
        )
        self.env_file.chmod(0o600)
        self._compose("down", "--volumes", "--remove-orphans", check=False)

    def tearDown(self) -> None:
        self._compose("down", "--volumes", "--remove-orphans", check=False)
        self.temporary_directory.cleanup()

    def _compose(
        self,
        *arguments: str,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                str(self.env_file),
                "--file",
                str(COMPOSE_FILE),
                "--project-name",
                PROJECT,
                *arguments,
            ],
            capture_output=True,
            text=True,
            env=self.environment,
            timeout=300,
        )
        if check and result.returncode != 0:
            self.fail(f"Compose command failed: {result.stderr[-4000:]}")
        return result

    def _recovery(
        self, *arguments: str, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [
                str(RECOVERY),
                "--env-file",
                str(self.env_file),
                "--compose-file",
                str(COMPOSE_FILE),
                "--project-name",
                PROJECT,
                "--journal-dir",
                str(self.root / "journal"),
                *arguments,
            ],
            capture_output=True,
            text=True,
            env=self.environment,
            timeout=900,
        )
        if check and result.returncode != 0:
            logs = self._compose("logs", "--no-color", "db", check=False).stdout[-6000:]
            self.fail(
                "Recovery command failed:\n"
                f"stdout={result.stdout[-3000:]}\n"
                f"stderr={result.stderr[-6000:]}\n"
                f"db_logs={logs}"
            )
        return result

    def _psql(self, sql: str) -> str:
        result = self._compose(
            "exec",
            "-T",
            "db",
            "psql",
            "--no-psqlrc",
            "--set=ON_ERROR_STOP=1",
            "--tuples-only",
            "--no-align",
            "--username",
            "postgres",
            "--dbname",
            "threatlens",
            "--command",
            sql,
        )
        return result.stdout.strip()

    def test_backup_drill_and_destructive_restore_preserve_invariants(self) -> None:
        self._compose("up", "--detach", "--wait", "db", "redis")
        self._compose("run", "--rm", "--no-deps", "api", "alembic", "upgrade", "head")
        self._psql(
            "CREATE TABLE recovery_e2e_marker (value text NOT NULL);"
            "INSERT INTO recovery_e2e_marker (value) VALUES ('before-backup');"
            "INSERT INTO feeds (id, name, url, url_digest, enabled) VALUES ("
            "'10000000-0000-0000-0000-000000000001', 'Recovery feed', "
            "'encrypted-placeholder', repeat('a', 64), true);"
            "INSERT INTO ai_settings ("
            "id, singleton_key, company_regions_json, company_stack_json, "
            "company_priority_topics_json, company_keywords_json, company_exclusions_json"
            ") VALUES ("
            "'20000000-0000-0000-0000-000000000001', 1, '[]'::json, '[]'::json, "
            "'[]'::json, '[]'::json, '[]'::json);"
        )
        self._psql(
            "CREATE ROLE recovery_e2e_reader NOLOGIN;"
            "GRANT CONNECT ON DATABASE threatlens TO recovery_e2e_reader;"
            "ALTER DATABASE threatlens SET statement_timeout = '17s';"
        )
        self._compose(
            "exec",
            "-T",
            "redis",
            "sh",
            "-ceu",
            'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli --no-auth-warning SET recovery-e2e stale',
        )

        backup = self._recovery(
            "backup",
            "--output-dir",
            str(self.backup_directory),
        ).stdout.strip()
        self.assertTrue((Path(backup) / "manifest.json").is_file())
        self._recovery("verify", "--backup", backup)
        self._recovery("drill", "--backup", backup)
        ledger_scope_id = self._psql(
            "SELECT metadata_json->>'ledger_scope_id' FROM system_operation_runs "
            "WHERE operation_type = 'backup' ORDER BY started_at DESC LIMIT 1;"
        )
        self.assertRegex(ledger_scope_id, r"^[0-9a-f]{64}$")
        interrupted_id = "30000000-0000-4000-8000-000000000001"
        unrelated_id = "30000000-0000-4000-8000-000000000002"
        self._psql(
            "INSERT INTO system_operation_runs ("
            "id, operation_type, status, initiated_by, source, metadata_json, started_at"
            ") VALUES ("
            f"'{interrupted_id}', 'verify', 'running', 'host-operator', "
            "'host-recovery-cli', "
            f"jsonb_build_object('ledger_scope_id', '{ledger_scope_id}'), now() - interval '2 hours'"
            "), ("
            f"'{unrelated_id}', 'verify', 'running', 'host-operator', "
            "'host-recovery-cli', jsonb_build_object('ledger_scope_id', repeat('f', 64)), "
            "now() - interval '2 hours');"
        )
        self._recovery("verify", "--backup", backup)
        self.assertEqual(
            self._psql(
                "SELECT status || '|' || error_code || '|' || "
                "(metadata_json->>'reconciled_after_interruption') "
                f"FROM system_operation_runs WHERE id = '{interrupted_id}';"
            ),
            "failed|operation_interrupted|true",
        )
        self.assertEqual(
            self._psql(
                f"SELECT status FROM system_operation_runs WHERE id = '{unrelated_id}';"
            ),
            "running",
        )
        self.assertEqual(
            self._psql(
                "SELECT count(*) FROM system_operation_runs WHERE status = 'running' "
                f"AND id <> '{unrelated_id}';"
            ),
            "0",
        )

        self._psql("UPDATE recovery_e2e_marker SET value = 'after-backup';")
        confirmation = self._recovery(
            "restore",
            "--backup",
            backup,
            "--show-confirmation",
        ).stdout.strip()
        restore = self._recovery(
            "restore",
            "--backup",
            backup,
            "--confirm",
            confirmation,
            "--acknowledge-data-loss",
            "--safety-backup-dir",
            str(self.backup_directory / "safety"),
        )

        self.assertIn("RESTORE_STATUS=completed_quarantined", restore.stdout)
        self.assertEqual(
            self._psql("SELECT value FROM recovery_e2e_marker;"), "before-backup"
        )
        self.assertEqual(
            self._psql("SELECT enabled::text FROM feeds LIMIT 1;"), "false"
        )
        self.assertEqual(
            self._psql(
                "SELECT (summary_enabled OR relevance_enabled OR daily_brief_enabled "
                "OR reporting_enabled OR auto_enrich_new_items)::text FROM ai_settings LIMIT 1;"
            ),
            "false",
        )
        self.assertEqual(
            self._psql(
                "SELECT count(*) FROM pg_catalog.pg_database "
                "WHERE datname LIKE 'tl_pre_restore_%';"
            ),
            "0",
        )
        self.assertEqual(
            self._psql(
                "SELECT count(*) FROM pg_catalog.pg_roles WHERE rolname LIKE 'tl_recovery_%';"
            ),
            "0",
        )
        self.assertEqual(
            self._psql(
                "SELECT datallowconn::text FROM pg_catalog.pg_database "
                "WHERE datname = 'threatlens';"
            ),
            "true",
        )
        self.assertEqual(
            self._psql(
                "SELECT rolcanlogin::text FROM pg_catalog.pg_roles WHERE rolname = 'postgres';"
            ),
            "true",
        )
        self.assertEqual(self._psql("SHOW statement_timeout;"), "17s")
        self.assertEqual(
            self._psql(
                "SELECT has_database_privilege("
                "'recovery_e2e_reader', 'threatlens', 'CONNECT')::text;"
            ),
            "true",
        )
        if (
            self._psql(
                "SELECT to_regclass('public.system_operation_runs') IS NOT NULL;"
            )
            == "t"
        ):
            self.assertEqual(
                self._psql(
                    "SELECT status FROM system_operation_runs "
                    "WHERE operation_type = 'restore' ORDER BY started_at DESC LIMIT 1;"
                ),
                "succeeded",
            )
        redis_result = self._compose(
            "exec",
            "-T",
            "redis",
            "sh",
            "-ceu",
            'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli --no-auth-warning EXISTS recovery-e2e',
        )
        self.assertEqual(redis_result.stdout.strip(), "0")


if __name__ == "__main__":
    unittest.main()
