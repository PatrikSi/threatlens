from __future__ import annotations

import os
import secrets
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RECOVERY = REPOSITORY_ROOT / "scripts" / "recovery" / "threatlens-recovery.sh"
COMPOSE_FILE = REPOSITORY_ROOT / "tests" / "recovery" / "docker-compose.e2e.yml"
PROJECT = f"threatlens-recovery-e2e-{secrets.token_hex(6)}"


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
            "RECOVERY_E2E_RUNTIME_PASSWORD": secrets.token_hex(24),
            "RECOVERY_E2E_MIGRATION_PASSWORD": secrets.token_hex(24),
            "RECOVERY_E2E_REDIS_PASSWORD": secrets.token_hex(24),
        }
        self.environment.update(values)
        self.env_file.write_text(
            "".join(f"{key}={value}\n" for key, value in sorted(values.items())),
            encoding="utf-8",
        )
        self.env_file.chmod(0o600)

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

    def _assert_failed_restore_recovers_role_fences(self, backup: str) -> None:
        original_oid = self._psql("SELECT oid FROM pg_database WHERE datname = 'threatlens';")
        hook = self.root / "fail-after-fence.sh"
        real_hook = REPOSITORY_ROOT / "scripts/recovery/post_restore_quarantine.sh"
        hook.write_text(
            '#!/bin/sh\nif [ "$1" = apply ]; then exit 42; fi\n'
            f'exec {shlex.quote(str(real_hook))} "$@"\n', encoding="utf-8",
        )
        hook.chmod(0o700)
        confirmation = self._recovery(
            "restore", "--backup", backup, "--show-confirmation",
        ).stdout.strip()
        failed = self._recovery(
            "restore", "--backup", backup, "--confirm", confirmation,
            "--acknowledge-data-loss", "--safety-backup-dir", str(self.backup_directory / "rollback-safety"),
            "--quarantine-hook", str(hook), check=False,
        )
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("E808", failed.stderr)
        self.assertEqual(self._psql("SELECT oid FROM pg_database WHERE datname = 'threatlens';"), original_oid)
        self.assertEqual(self._psql("SELECT value FROM recovery_e2e_marker;"), "after-backup")
        self.assertEqual(self._psql(
            "SELECT count(*) FROM pg_roles WHERE rolname IN ('threatlens_runtime', 'threatlens_migration') AND rolcanlogin;"
        ), "2")
        self.assertEqual(self._psql(
            "SELECT count(*) FROM pg_roles WHERE rolname LIKE 'tl_recovery_%';"
        ), "0")

    def test_offline_upgrade_preserves_existing_rows_and_other_database_owners(self) -> None:
        self._compose("up", "--detach", "--wait", "db", "redis")
        self._psql(
            "ALTER DATABASE threatlens OWNER TO postgres; ALTER SCHEMA public OWNER TO postgres;"
            "CREATE TABLE legacy_marker (value text NOT NULL);"
            "INSERT INTO legacy_marker VALUES ('retained');"
        )
        self._psql("CREATE DATABASE unrelated_synthetic OWNER postgres;")
        command = [
            "python3", str(REPOSITORY_ROOT / "scripts/database/upgrade-roles.py"),
            "--env-file", str(self.env_file), "--file", str(COMPOSE_FILE), "--project-name", PROJECT,
        ]
        self._psql("ALTER ROLE threatlens_runtime CREATEROLE;")
        refused = subprocess.run(command, capture_output=True, text=True, env=self.environment, timeout=120)
        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual(self._psql(
            "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = 'threatlens';"
        ), "postgres")
        for key in ("RECOVERY_E2E_RUNTIME_PASSWORD", "RECOVERY_E2E_MIGRATION_PASSWORD"):
            self.assertNotIn(self.environment[key], refused.stdout + refused.stderr)
        self._psql("ALTER ROLE threatlens_runtime NOCREATEROLE;")
        result = subprocess.run(command, capture_output=True, text=True, env=self.environment, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Application services remain stopped", result.stdout)
        self.assertEqual(self._psql("SELECT value FROM legacy_marker;"), "retained")
        self.assertEqual(self._psql(
            "SELECT pg_get_userbyid(relowner) FROM pg_class WHERE relname = 'legacy_marker';"
        ), "threatlens_migration")
        self.assertEqual(self._psql(
            "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = 'unrelated_synthetic';"
        ), "postgres")
        self.assertEqual(self._psql(
            "SELECT has_table_privilege('threatlens_runtime', 'legacy_marker', 'UPDATE')::text;"
        ), "true")
        self._compose("run", "--rm", "--no-deps", "migrate")

    def test_offline_upgrade_preserves_owned_and_standalone_sequences(self) -> None:
        self._compose("up", "--detach", "--wait", "db", "redis")
        self._psql(
            "ALTER DATABASE threatlens OWNER TO postgres; ALTER SCHEMA public OWNER TO postgres;"
            # Create this sequence before its table to exercise the failing catalog order.
            "CREATE SEQUENCE legacy_owned_id_seq START 101;"
            "CREATE TABLE legacy_owned (id bigint PRIMARY KEY DEFAULT nextval('legacy_owned_id_seq'));"
            "ALTER SEQUENCE legacy_owned_id_seq OWNED BY legacy_owned.id;"
            "CREATE TABLE legacy_serial (id bigserial PRIMARY KEY);"
            "CREATE TABLE legacy_identity (id bigint GENERATED ALWAYS AS IDENTITY (START WITH 301) PRIMARY KEY);"
            "CREATE TABLE legacy_default_identity (id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY);"
            "CREATE SEQUENCE legacy_standalone START 401;"
            "CREATE TABLE legacy_reference (id bigint DEFAULT nextval('legacy_standalone'));"
            "INSERT INTO legacy_owned DEFAULT VALUES;"
            "INSERT INTO legacy_serial DEFAULT VALUES;"
            "INSERT INTO legacy_identity DEFAULT VALUES;"
            "INSERT INTO legacy_default_identity DEFAULT VALUES;"
            "INSERT INTO legacy_reference DEFAULT VALUES;"
            "CREATE SCHEMA unrelated_schema; CREATE TABLE unrelated_schema.marker (id serial);"
        )
        self._psql("CREATE DATABASE unrelated_synthetic OWNER postgres;")
        dependencies = self._psql(
            "SELECT pg_get_serial_sequence(name, 'id') FROM "
            "(VALUES ('legacy_owned'), ('legacy_serial'), ('legacy_identity'), ('legacy_default_identity')) "
            "AS tables(name) ORDER BY name;"
        )
        command = [
            "python3", str(REPOSITORY_ROOT / "scripts/database/upgrade-roles.py"),
            "--env-file", str(self.env_file), "--file", str(COMPOSE_FILE), "--project-name", PROJECT,
        ]
        # A successful cutover must also be safe to repeat after an uncertain result.
        for attempt in range(2):
            with self.subTest(attempt=attempt):
                result = subprocess.run(command, capture_output=True, text=True, env=self.environment, timeout=120)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self._psql(
                    "SELECT count(*) FROM pg_class AS relation "
                    "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
                    "WHERE namespace.nspname = 'public' AND relation.relkind IN ('r', 'S') "
                    "AND pg_get_userbyid(relation.relowner) = 'threatlens_migration';"
                ), "10")
                self.assertEqual(self._psql(
                    "SELECT pg_get_serial_sequence(name, 'id') FROM "
                    "(VALUES ('legacy_owned'), ('legacy_serial'), ('legacy_identity'), ('legacy_default_identity')) "
                    "AS tables(name) ORDER BY name;"
                ), dependencies)
                self.assertEqual(self._psql(
                    "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = 'unrelated_synthetic';"
                ), "postgres")
                self.assertEqual(self._psql(
                    "SELECT count(*) FROM pg_class AS relation "
                    "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
                    "WHERE namespace.nspname = 'unrelated_schema' AND relation.relkind IN ('r', 'S') "
                    "AND pg_get_userbyid(relation.relowner) = 'postgres';"
                ), "2")
        self._psql(
            "SET ROLE threatlens_runtime;"
            "INSERT INTO legacy_owned DEFAULT VALUES;"
            "INSERT INTO legacy_serial DEFAULT VALUES;"
            "INSERT INTO legacy_identity DEFAULT VALUES;"
            "INSERT INTO legacy_default_identity DEFAULT VALUES;"
            "INSERT INTO legacy_reference DEFAULT VALUES;"
        )
        self.assertEqual(self._psql(
            "SELECT 'owned', array_agg(id ORDER BY id)::text FROM legacy_owned UNION ALL "
            "SELECT 'serial', array_agg(id ORDER BY id)::text FROM legacy_serial UNION ALL "
            "SELECT 'identity', array_agg(id ORDER BY id)::text FROM legacy_identity UNION ALL "
            "SELECT 'default_identity', array_agg(id ORDER BY id)::text FROM legacy_default_identity UNION ALL "
            "SELECT 'reference', array_agg(id ORDER BY id)::text FROM legacy_reference;"
        ), "owned|{101,102}\nserial|{1,2}\nidentity|{301,302}\ndefault_identity|{1,2}\nreference|{401,402}")
        self.assertEqual(self._psql("SELECT last_value FROM legacy_standalone;"), "402")
        self.assertEqual(self._psql(
            "SELECT has_schema_privilege('threatlens_runtime', 'public', 'CREATE')::text;"
        ), "false")

    def test_split_role_migrations_round_trip_without_runtime_schema_privileges(self) -> None:
        self._compose("up", "--detach", "--wait", "db", "redis")
        self._compose("run", "--rm", "--no-deps", "migrate")
        head = self._psql("SELECT version_num FROM alembic_version;")
        self._compose("run", "--rm", "--no-deps", "migrate", "alembic", "downgrade", "0090_lifecycle_scan_cursors")
        self.assertEqual(self._psql("SELECT version_num FROM alembic_version;"), "0090_lifecycle_scan_cursors")
        self._compose("run", "--rm", "--no-deps", "migrate")
        self.assertEqual(self._psql("SELECT version_num FROM alembic_version;"), head)
        self.assertEqual(self._psql(
            "SELECT has_schema_privilege('threatlens_runtime', 'public', 'CREATE')::text;"
        ), "false")
        self.assertEqual(self._psql(
            "SELECT count(*) FROM pg_class AS c JOIN pg_namespace AS n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relkind='r' AND pg_get_userbyid(c.relowner)<>'threatlens_migration';"
        ), "0")
        self._compose("run", "--rm", "--no-deps", "api", "python", "-m", "app.scripts.seed_admin")

    def test_backup_drill_and_destructive_restore_preserve_invariants(self) -> None:
        self._compose("up", "--detach", "--wait", "db", "redis")
        self._compose("run", "--rm", "--no-deps", "migrate")
        self._compose("run", "--rm", "--no-deps", "api", "python", "-m", "app.scripts.seed_admin")
        self.assertEqual(self._psql(
            "SELECT rolname || '|' || rolsuper::text || '|' || rolcreaterole::text || '|' || rolcreatedb::text "
            "FROM pg_roles WHERE rolname IN ('threatlens_runtime', 'threatlens_migration') ORDER BY rolname;"
        ), "threatlens_migration|false|false|false\nthreatlens_runtime|false|false|false")
        self.assertEqual(self._psql(
            "SELECT has_schema_privilege('threatlens_runtime', 'public', 'CREATE')::text;"
        ), "false")
        denied = self._compose("run", "--rm", "--no-deps", "api", "alembic", "downgrade", "-1", check=False)
        self.assertNotEqual(denied.returncode, 0)
        self._compose("run", "--rm", "--no-deps", "api", "python", "-c", """
from datetime import datetime, timedelta, timezone
import uuid
from app.db.session import SessionLocal
from app.models.audit_log import AuditLog, AuditLogDataAccessFeed
from app.services.lifecycle_permission_pruning import prune_permission_history_parent
from app.services.lifecycle_pruning_contracts import PruningContext
with SessionLocal() as db:
    cutoff = datetime.now(timezone.utc) - timedelta(days=1)
    row = AuditLog(action='recovery.partial.retention', resource_type='test_fixture',
                   metadata_json={}, created_at=cutoff - timedelta(days=1))
    db.add(row)
    db.flush()
    db.add_all(AuditLogDataAccessFeed(audit_log_id=row.id, source_feed_id_snapshot=uuid.uuid4()) for _ in range(2))
    db.flush()
    result = prune_permission_history_parent(db, model=AuditLog, parent_id=row.id,
        context=PruningContext(cutoff, AuditLog.created_at < cutoff), limit=1)
    assert result.children_pruned == 1
    db.commit()
""")
        self._psql(
            "SET ROLE threatlens_migration; CREATE TABLE recovery_e2e_marker (value text NOT NULL);"
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
        self._assert_failed_restore_recovers_role_fences(backup)
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
        self.assertEqual(self._psql(
            "SELECT count(*) FROM audit_logs AS a JOIN lifecycle_pruning_records AS p "
            "ON p.dataset='audit_logs' AND p.parent_id=a.id "
            "WHERE a.action='recovery.partial.retention' "
            "AND a.retention_pruning_started_at IS NOT NULL AND p.children_pruned=1;"
        ), "1")

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
        self.assertEqual(self._psql(
            "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = 'threatlens';"
        ), "threatlens_migration")
        self.assertEqual(self._psql(
            "SELECT rolcanlogin::text FROM pg_roles WHERE rolname = 'threatlens_migration';"
        ), "true")
        self.assertEqual(self._psql(
            "SELECT has_table_privilege('threatlens_runtime', 'recovery_e2e_marker', 'UPDATE')::text;"
        ), "true")
        self._compose("run", "--rm", "--no-deps", "migrate")
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
