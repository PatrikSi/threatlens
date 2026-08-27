from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SAFETY = REPOSITORY_ROOT / "scripts" / "recovery" / "recovery_safety.py"


def _compose_document() -> dict:
    database_url = "postgresql+psycopg://threatlens:db-secret@db:5432/threatlens"
    redis_url = "redis://:redis-secret@redis:6379/0"
    backend = {
        "environment": {"DATABASE_URL": database_url, "REDIS_URL": redis_url},
        "networks": {"backplane": None},
    }
    return {
        "services": {
            "db": {
                "image": "postgres:16",
                "environment": {
                    "POSTGRES_DB": "threatlens",
                    "POSTGRES_USER": "threatlens",
                    "POSTGRES_PASSWORD": "db-secret",
                },
                "networks": {"backplane": None},
            },
            "redis": {
                "image": "redis:7-alpine",
                "environment": {"REDIS_PASSWORD": "redis-secret"},
                "networks": {"backplane": None},
            },
            "api": backend,
            "worker": backend,
        }
    }


class RecoverySafetyTests(unittest.TestCase):
    def _run(
        self,
        *arguments: str,
        input_text: str | None = None,
        **environment: str,
    ) -> subprocess.CompletedProcess[str]:
        process_environment = dict(os.environ)
        process_environment.update(environment)
        return subprocess.run(
            [str(SAFETY), *arguments],
            input=input_text,
            capture_output=True,
            text=True,
            env=process_environment,
        )

    def test_validate_target_accepts_only_matching_local_services(self) -> None:
        result = self._run(
            "validate-target", input_text=json.dumps(_compose_document())
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        fields = result.stdout.splitlines()
        self.assertEqual(fields[:3], ["threatlens", "threatlens", "0"])
        self.assertRegex(fields[3], r"^[0-9a-f]{64}$")
        self.assertEqual(fields[4], "api,worker")
        self.assertNotIn("secret", result.stdout)

    def test_validate_target_preserves_quoted_sql_identifiers_as_data(self) -> None:
        document = _compose_document()
        database = 'threat"lens'
        database_user = "threat'lens;role"
        password = "db-secret"
        document["services"]["db"]["environment"]["POSTGRES_DB"] = database
        document["services"]["db"]["environment"]["POSTGRES_USER"] = database_user
        database_url = (
            "postgresql+psycopg://"
            f"{quote(database_user, safe='')}:{quote(password, safe='')}@"
            f"db:5432/{quote(database, safe='')}"
        )
        for service_name in ("api", "worker"):
            document["services"][service_name]["environment"] = dict(
                document["services"][service_name]["environment"]
            )
            document["services"][service_name]["environment"]["DATABASE_URL"] = (
                database_url
            )

        result = self._run("validate-target", input_text=json.dumps(document))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[:2], [database, database_user])

    def test_validate_target_rejects_unrecognized_backend_accessor(self) -> None:
        document = _compose_document()
        document["services"]["custom-consumer"] = {
            "environment": dict(document["services"]["api"]["environment"]),
            "networks": {"backplane": None},
        }

        result = self._run("validate-target", input_text=json.dumps(document))

        self.assertEqual(result.returncode, 4)
        self.assertIn("unrecognized backend data accessor", result.stderr)

    def test_validate_runtime_rejects_running_environment_drift(self) -> None:
        document = _compose_document()
        inspected = []
        for service_name, environment in (
            ("db", document["services"]["db"]["environment"]),
            ("redis", document["services"]["redis"]["environment"]),
            ("api", document["services"]["api"]["environment"]),
        ):
            runtime_environment = dict(environment)
            if service_name == "api":
                runtime_environment["DATABASE_URL"] = runtime_environment[
                    "DATABASE_URL"
                ].replace("@db:", "@stale-db:")
            inspected.append(
                {
                    "Config": {
                        "Labels": {"com.docker.compose.service": service_name},
                        "Env": [
                            f"{key}={value}"
                            for key, value in runtime_environment.items()
                        ],
                    }
                }
            )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            compose_path = root / "compose.json"
            inspect_path = root / "inspect.json"
            compose_path.write_text(json.dumps(document), encoding="utf-8")
            inspect_path.write_text(json.dumps(inspected), encoding="utf-8")
            compose_path.chmod(0o600)
            inspect_path.chmod(0o600)

            result = self._run(
                "validate-runtime",
                "--compose-config",
                str(compose_path),
                "--inspect",
                str(inspect_path),
            )

        self.assertEqual(result.returncode, 4)
        self.assertIn("api container DATABASE_URL differs", result.stderr)
        self.assertNotIn("db-secret", result.stderr)

    def test_validate_target_rejects_external_database(self) -> None:
        document = _compose_document()
        document["services"]["api"]["environment"]["DATABASE_URL"] = (
            "postgresql+psycopg://threatlens:db-secret@database.example:5432/threatlens"
        )

        result = self._run("validate-target", input_text=json.dumps(document))

        self.assertEqual(result.returncode, 4)
        self.assertIn("does not target local service db:5432", result.stderr)
        self.assertNotIn("db-secret", result.stderr)

    def test_validate_target_rejects_nonzero_redis_database(self) -> None:
        document = _compose_document()
        document["services"]["worker"]["environment"] = dict(
            document["services"]["worker"]["environment"]
        )
        document["services"]["worker"]["environment"]["REDIS_URL"] = (
            "redis://:redis-secret@redis:6379/4"
        )

        result = self._run("validate-target", input_text=json.dumps(document))

        self.assertEqual(result.returncode, 4)
        self.assertIn("only local database 0 is supported", result.stderr)

    def test_validate_target_rejects_password_drift_without_disclosing_it(self) -> None:
        document = _compose_document()
        document["services"]["api"]["environment"]["DATABASE_URL"] = (
            "postgresql+psycopg://threatlens:wrong-secret@db:5432/threatlens"
        )

        result = self._run("validate-target", input_text=json.dumps(document))

        self.assertEqual(result.returncode, 4)
        self.assertIn("password differs", result.stderr)
        self.assertNotIn("wrong-secret", result.stderr)
        self.assertNotIn("db-secret", result.stderr)

    def test_stage_uses_private_files_and_pins_approved_digests(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "stage"
            destination.mkdir(mode=0o700)
            manifest = root / "manifest.json"
            manifest.write_text("{}\n", encoding="utf-8")
            archive = root / "database.dump"
            archive.write_bytes(b"approved archive")
            hook = root / "hook.sh"
            hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            helper = root / "helper.py"
            helper.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            archive_sha = hashlib.sha256(archive.read_bytes()).hexdigest()
            manifest_sha = hashlib.sha256(manifest.read_bytes()).hexdigest()
            hook_sha = hashlib.sha256(hook.read_bytes()).hexdigest()
            helper_sha = hashlib.sha256(helper.read_bytes()).hexdigest()

            result = self._run(
                "stage",
                "--manifest",
                str(manifest),
                "--archive",
                str(archive),
                "--hook",
                str(hook),
                "--manifest-helper",
                str(helper),
                "--destination",
                str(destination),
                "--expected-manifest-sha256",
                manifest_sha,
                "--expected-archive-sha256",
                archive_sha,
                "--expected-hook-sha256",
                hook_sha,
                "--expected-helper-sha256",
                helper_sha,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                (destination / "database.dump").read_bytes(), b"approved archive"
            )
            self.assertEqual(
                (destination / "manifest.json").stat().st_mode & 0o777, 0o600
            )
            self.assertEqual(
                (destination / "database.dump").stat().st_mode & 0o777, 0o600
            )
            self.assertEqual(
                (destination / "quarantine-hook").stat().st_mode & 0o777, 0o700
            )
            self.assertEqual(
                (destination / "recovery_manifest.py").stat().st_mode & 0o777, 0o700
            )

    def test_stage_rejects_manifest_changed_after_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "stage"
            destination.mkdir(mode=0o700)
            manifest = root / "manifest.json"
            manifest.write_text("{}\n", encoding="utf-8")
            archive = root / "database.dump"
            archive.write_bytes(b"archive")
            hook = root / "hook"
            hook.write_text("hook", encoding="utf-8")
            helper = root / "helper"
            helper.write_text("helper", encoding="utf-8")

            result = self._run(
                "stage",
                "--manifest",
                str(manifest),
                "--archive",
                str(archive),
                "--hook",
                str(hook),
                "--manifest-helper",
                str(helper),
                "--destination",
                str(destination),
                "--expected-manifest-sha256",
                hashlib.sha256(b"approved manifest").hexdigest(),
                "--expected-archive-sha256",
                hashlib.sha256(archive.read_bytes()).hexdigest(),
                "--expected-hook-sha256",
                hashlib.sha256(hook.read_bytes()).hexdigest(),
                "--expected-helper-sha256",
                hashlib.sha256(helper.read_bytes()).hexdigest(),
            )

            self.assertEqual(result.returncode, 4)
            self.assertIn("Manifest changed after verification", result.stderr)

    def test_stage_rejects_symlink_sources_and_digest_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "stage"
            destination.mkdir(mode=0o700)
            real_archive = root / "real.dump"
            real_archive.write_bytes(b"replacement")
            archive = root / "database.dump"
            archive.symlink_to(real_archive)
            manifest = root / "manifest.json"
            manifest.write_text("{}", encoding="utf-8")
            hook = root / "hook"
            hook.write_text("hook", encoding="utf-8")
            helper = root / "helper"
            helper.write_text("helper", encoding="utf-8")

            result = self._run(
                "stage",
                "--manifest",
                str(manifest),
                "--archive",
                str(archive),
                "--hook",
                str(hook),
                "--manifest-helper",
                str(helper),
                "--destination",
                str(destination),
                "--expected-manifest-sha256",
                hashlib.sha256(manifest.read_bytes()).hexdigest(),
                "--expected-archive-sha256",
                "0" * 64,
                "--expected-hook-sha256",
                hashlib.sha256(hook.read_bytes()).hexdigest(),
                "--expected-helper-sha256",
                hashlib.sha256(helper.read_bytes()).hexdigest(),
            )

            self.assertEqual(result.returncode, 4)
            self.assertIn("without following links", result.stderr)

    def test_stage_rejects_regular_archive_replaced_after_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "stage"
            destination.mkdir(mode=0o700)
            manifest = root / "manifest.json"
            manifest.write_text("{}", encoding="utf-8")
            archive = root / "database.dump"
            archive.write_bytes(b"replacement archive")
            hook = root / "hook"
            hook.write_text("hook", encoding="utf-8")
            helper = root / "helper"
            helper.write_text("helper", encoding="utf-8")

            result = self._run(
                "stage",
                "--manifest",
                str(manifest),
                "--archive",
                str(archive),
                "--hook",
                str(hook),
                "--manifest-helper",
                str(helper),
                "--destination",
                str(destination),
                "--expected-manifest-sha256",
                hashlib.sha256(manifest.read_bytes()).hexdigest(),
                "--expected-archive-sha256",
                hashlib.sha256(b"approved archive").hexdigest(),
                "--expected-hook-sha256",
                hashlib.sha256(hook.read_bytes()).hexdigest(),
                "--expected-helper-sha256",
                hashlib.sha256(helper.read_bytes()).hexdigest(),
            )

            self.assertEqual(result.returncode, 4)
            self.assertIn("changed after verification", result.stderr)

    def test_smoke_environment_excludes_outbound_credentials(self) -> None:
        document = _compose_document()
        document["services"]["api"]["environment"].update(
            {
                "AI_API_KEY": "must-not-be-copied",
                "APP_DATA_ENCRYPTION_KEY": "current-encryption-key",
            }
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "smoke.env"
            result = self._run(
                "write-smoke-env",
                "--output",
                str(output),
                "--database-host",
                "recovery-db",
                "--database",
                "threatlens",
                input_text=json.dumps(document),
                THREATLENS_RECOVERY_DATABASE_USER="recovery_role",
                THREATLENS_RECOVERY_DATABASE_PASSWORD="ephemeral-password",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            content = output.read_text(encoding="utf-8")
            self.assertIn("AI_ENABLED=false", content)
            self.assertIn("ALLOW_PRIVATE_NETWORK_FETCH=false", content)
            self.assertIn("APP_DATA_ENCRYPTION_KEY=current-encryption-key", content)
            self.assertNotIn("must-not-be-copied", content)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
