from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
EXPORTER = ROOT / "scripts/export_kubernetes_secret.py"


class KubernetesEnvironmentExportTests(unittest.TestCase):
    """Exercise export through real Compose without a daemon or a cluster."""

    @classmethod
    def setUpClass(cls) -> None:
        unavailable = AssertionError if os.environ.get("GITHUB_ACTIONS") == "true" else unittest.SkipTest
        message = "Docker Compose is required for Kubernetes environment export checks"
        if shutil.which("docker") is None:
            raise unavailable(message)
        result = subprocess.run(
            ["docker", "compose", "version"], capture_output=True, check=False, timeout=30
        )
        if result.returncode:
            raise unavailable(message)

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory(prefix="threatlens-kubernetes-env-")
        self.addCleanup(self.directory.cleanup)
        self.work = Path(self.directory.name)
        self.binary_directory = self.work / "bin"
        self.binary_directory.mkdir()
        kubectl = self.binary_directory / "kubectl"
        kubectl.write_text(
            "#!/bin/sh\n"
            f"touch '{self.work / 'unexpected-cluster-access'}'\n"
            "exit 99\n"
        )
        kubectl.chmod(0o755)
        self.environment = {
            "PATH": f"{self.binary_directory}:{os.environ.get('PATH', os.defpath)}",
            "HOME": str(self.work),
            "LANG": "C.UTF-8",
            "DOCKER_CONFIG": str(self.work / "docker-config"),
            "DOCKER_HOST": f"unix://{self.work}/unavailable-docker.sock",
            "TMPDIR": str(self.work),
            "ADMIN_EMAIL": "kubernetes-test@example.com",
            "ADMIN_PASSWORD": "kubernetes-test-administrator-password",
        }
        self.env_file = self.work / "existing.env"
        generated = self.run_command([str(ROOT / "bootstrap.sh"), str(self.env_file)])
        self.assertEqual(generated.returncode, 0, "Unable to create isolated bootstrap fixture")

    def tearDown(self) -> None:
        self.assertFalse((self.work / "unexpected-cluster-access").exists())

    def run_command(self, arguments: list[str], **overrides: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            arguments,
            cwd=self.work,
            env=self.environment | overrides,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def set_values(self, values: dict[str, str]) -> None:
        text = self.env_file.read_text()
        for key, value in values.items():
            escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "$$")
            replacement = f'{key}="{escaped}"'
            text, count = re.subn(rf"^{key}=.*$", lambda match: replacement, text, flags=re.MULTILINE)
            self.assertEqual(count, 1, f"Missing or duplicate fixture setting: {key}")
        self.env_file.write_text(text)

    def export(self, service: str = "api", *arguments: str, **overrides: str) -> subprocess.CompletedProcess:
        return self.run_command(
            [
                sys.executable, str(EXPORTER), "--env-file", str(self.env_file),
                "--service", service, "--name", "threatlens-environment", *arguments,
            ],
            **overrides,
        )

    def export_document(self, service: str = "api", *arguments: str, **overrides: str) -> dict:
        result = self.export(service, *arguments, **overrides)
        self.assertEqual(result.returncode, 0, "Environment export failed")
        return json.loads(result.stdout)

    def configuration(self) -> dict:
        result = self.run_command([
            "docker", "compose", "--env-file", str(self.env_file),
            "--file", str(ROOT / "docker-compose.yml"), "config", "--format", "json",
        ])
        self.assertEqual(result.returncode, 0, "Unable to resolve canonical Compose fixture")
        return json.loads(result.stdout)

    def assert_redacted_failure(self, result: subprocess.CompletedProcess) -> None:
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("sensitive-fixture-value", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertTrue(result.stderr.strip())

    def test_export_preserves_existing_file_and_never_contacts_a_cluster(self) -> None:
        before = self.env_file.read_bytes()
        before_stat = self.env_file.stat()
        document = self.export_document("api", "--namespace", "threatlens-team")

        self.assertEqual(document["apiVersion"], "v1")
        self.assertEqual(document["kind"], "Secret")
        self.assertEqual(document["type"], "Opaque")
        self.assertEqual(document["metadata"], {
            "name": "threatlens-environment", "namespace": "threatlens-team",
        })
        self.assertEqual(self.env_file.read_bytes(), before)
        after_stat = self.env_file.stat()
        self.assertEqual(after_stat.st_ino, before_stat.st_ino)
        self.assertEqual(after_stat.st_mtime_ns, before_stat.st_mtime_ns)
        self.assertEqual(after_stat.st_mode, before_stat.st_mode)

    def test_every_service_exports_its_complete_resolved_environment(self) -> None:
        self.set_values({
            "AI_ENABLED": "true", "AI_API_KEY": "synthetic-provider-credential",
            "AI_API_KEY_BASE_URL": "https://generativelanguage.googleapis.com",
            "AI_RESPONSE_MAX_BYTES": "1234567", "DATABASE_POOL_SIZE": "3",
            "API_DATABASE_POOL_SIZE": "9", "EXPORT_DATABASE_POOL_SIZE": "5",
        })
        configuration = self.configuration()
        for service, settings in configuration["services"].items():
            with self.subTest(service=service):
                actual = self.export_document(service)["stringData"]
                expected = settings.get("environment", {})
                self.assertEqual(set(actual), set(expected))
                self.assertEqual(actual, {
                    key: value.replace("$$", "$") for key, value in expected.items()
                })
        api = self.export_document()["stringData"]
        self.assertEqual(api["AI_ENABLED"], "true")
        self.assertEqual(api["AI_API_KEY"], "synthetic-provider-credential")
        self.assertEqual(api["AI_API_KEY_BASE_URL"], "https://generativelanguage.googleapis.com")
        self.assertEqual(api["AI_RESPONSE_MAX_BYTES"], "1234567")
        self.assertEqual(api["DATABASE_POOL_SIZE"], "9")
        self.assertEqual(self.export_document("worker")["stringData"]["DATABASE_POOL_SIZE"], "3")
        self.assertEqual(self.export_document("worker-exports")["stringData"]["DATABASE_POOL_SIZE"], "5")

    def test_literals_survive_export_without_interpolation_or_shell_execution(self) -> None:
        marker = self.work / "must-not-be-created"
        value = f'''literal$ONE ${{TWO}} $$ $$$ $$$$ $(touch {marker}) `touch {marker}` ' " \\ trailing\\ unicode-žluťoučký-🔒'''
        self.set_values({"AI_API_KEY": value, "ADMIN_PASSWORD": value})
        values = self.export_document(
            "api", AI_API_KEY="ambient-value-must-not-override-file",
            ADMIN_PASSWORD="ambient-password-must-not-override-file",
        )["stringData"]
        self.assertEqual(values["AI_API_KEY"], value)
        self.assertEqual(values["ADMIN_PASSWORD"], value)
        self.assertFalse(marker.exists())

    def test_external_urls_and_separate_database_credentials_are_retained(self) -> None:
        runtime = "postgresql+psycopg://runtime:runtime$credential@postgres.database.svc:5432/threatlens"
        migration = "postgresql+psycopg://migration:migration$credential@postgres.database.svc:5432/threatlens"
        redis = "rediss://:cache$credential@redis.cache.svc:6380/2"
        self.set_values({
            "DATABASE_URL": runtime, "MIGRATION_DATABASE_URL": migration, "REDIS_URL": redis,
        })
        database = self.export_document("db")["stringData"]
        api = self.export_document("api")["stringData"]
        migrator = self.export_document("migrate")["stringData"]
        self.assertEqual(api["DATABASE_URL"], runtime)
        self.assertEqual(api["REDIS_URL"], redis)
        self.assertEqual(migrator["DATABASE_URL"], migration)
        for values in (api, migrator):
            self.assertNotIn("POSTGRES_PASSWORD", values)
            self.assertNotIn("POSTGRES_MIGRATION_PASSWORD", values)
            self.assertNotIn(database["POSTGRES_PASSWORD"], json.dumps(values))
            self.assertNotIn(database["POSTGRES_MIGRATION_PASSWORD"], json.dumps(values))
        self.assertNotIn("MIGRATION_DATABASE_URL", api)
        self.assertNotIn("ADMIN_PASSWORD", migrator)
        self.assertNotIn("JWT_SECRET", migrator)
        self.assertNotIn("APP_DATA_ENCRYPTION_KEY", migrator)

    def test_bundled_database_roles_remain_isolated(self) -> None:
        database = self.export_document("db")["stringData"]
        api = self.export_document("api")["stringData"]
        migrator = self.export_document("migrate")["stringData"]
        self.assertIn(database["POSTGRES_RUNTIME_PASSWORD"], api["DATABASE_URL"])
        self.assertIn(database["POSTGRES_MIGRATION_PASSWORD"], migrator["DATABASE_URL"])
        self.assertNotIn(database["POSTGRES_MIGRATION_PASSWORD"], json.dumps(api))
        self.assertNotIn(database["POSTGRES_PASSWORD"], json.dumps(api))
        self.assertNotIn(database["POSTGRES_PASSWORD"], json.dumps(migrator))
        self.assertNotIn(database["POSTGRES_RUNTIME_PASSWORD"], json.dumps(migrator))

    def test_invalid_names_and_missing_services_have_redacted_errors(self) -> None:
        for arguments in (
            ("--name", "Invalid_Name"), ("--name", "bad..name"),
            ("--name", "a" * 254), ("--namespace", "bad.namespace"),
            ("--namespace", "a" * 64), ("--service", "sensitive-fixture-value"),
        ):
            with self.subTest(arguments=arguments[:1]):
                self.assert_redacted_failure(self.export("api", *arguments))

    def test_missing_env_file_and_malformed_compose_do_not_leak_values(self) -> None:
        before = self.env_file.read_bytes()
        self.assert_redacted_failure(self.export("api", "--env-file", str(self.work / "missing.env")))
        malformed = self.work / "malformed-compose.yml"
        malformed.write_text('services:\n  api:\n    sensitive-fixture-value: [broken\n')
        self.assert_redacted_failure(self.export("api", "--compose-file", str(malformed)))
        self.assertEqual(self.env_file.read_bytes(), before)

    def test_malformed_compose_output_is_rejected_without_a_traceback(self) -> None:
        docker = self.binary_directory / "docker"
        for document in (
            [], {"services": None}, {"services": {"api": None}},
            {"services": {"api": "sensitive-fixture-value"}},
            {"services": {"api": {"environment": {"VALUE": None}}}},
            {"services": {"api": {"environment": {"bad/key": "sensitive-fixture-value"}}}},
        ):
            with self.subTest(shape=type(document).__name__):
                docker.write_text(
                    "#!/usr/bin/env python3\n"
                    f"print({json.dumps(document)!r})\n"
                )
                docker.chmod(0o755)
                self.assert_redacted_failure(self.export())


if __name__ == "__main__":
    unittest.main()
