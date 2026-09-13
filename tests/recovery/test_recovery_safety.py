from __future__ import annotations

from copy import deepcopy
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


def _deployment_identity_document() -> dict:
    return {
        "database": {
            "Id": "database-container",
            "Image": "sha256:database-image",
            "Name": "/review-db-1",
            "Mounts": [
                {"Type": "volume", "Name": "database-data", "Source": "/volumes/database-data", "Destination": "/var/lib/postgresql/data"},
                {"Type": "bind", "Source": "/review/provision.sh", "Destination": "/docker-entrypoint-initdb.d/provision.sh"},
            ],
        },
        "redis": {
            "Id": "redis-container",
            "Image": "sha256:redis-image",
            "Name": "/review-redis-1",
            "Mounts": [{"Type": "volume", "Name": "redis-data", "Source": "/volumes/redis-data", "Destination": "/data"}],
        },
    }


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

    def _identity(self, document: dict) -> subprocess.CompletedProcess[str]:
        return self._run(
            "identity", "--project", "review", "--database", "threatlens",
            "--target-config-sha256", "a" * 64, "--archive-sha256", "b" * 64,
            input_text=json.dumps(document),
        )

    def test_identity_ignores_inspection_mount_and_object_order(self) -> None:
        document = _deployment_identity_document()
        original = self._identity(document)
        document["database"]["Mounts"].reverse()
        reordered = json.loads(json.dumps(document, sort_keys=True))
        result = self._identity(reordered)
        self.assertEqual(original.returncode, 0, original.stderr)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, original.stdout)

    def test_identity_binds_every_container_and_mount_component(self) -> None:
        document = _deployment_identity_document()
        original = self._identity(document)
        self.assertEqual(original.returncode, 0, original.stderr)
        for service in ("database", "redis"):
            for key in ("Id", "Image", "Name", "Type", "Source", "Destination", "volume_name"):
                with self.subTest(service=service, component=key):
                    changed = deepcopy(document)
                    if key in {"Id", "Image", "Name"}:
                        changed[service][key] += "-changed"
                    else:
                        changed[service]["Mounts"][0]["Name" if key == "volume_name" else key] += "-changed"
                    result = self._identity(changed)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertNotEqual(result.stdout, original.stdout)

    def test_identity_distinguishes_delimiters_inside_mount_paths(self) -> None:
        document = _deployment_identity_document()
        mount = document["database"]["Mounts"][1]
        mount.update(Source="/source:/a", Destination="/destination;part")
        first = self._identity(document)
        mount.update(Source="/source", Destination="/a:/destination;part")
        second = self._identity(document)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertNotEqual(first.stdout, second.stdout)

    def test_identity_preserves_tmpfs_mounts_without_a_host_source(self) -> None:
        document = _deployment_identity_document()
        mount = {"Type": "tmpfs", "Source": "", "Destination": "/run"}
        document["database"]["Mounts"].append(mount)
        explicit_empty = self._identity(document)
        del mount["Source"]
        omitted = self._identity(document)
        self.assertEqual(explicit_empty.returncode, 0, explicit_empty.stderr)
        self.assertEqual(omitted.returncode, 0, omitted.stderr)
        self.assertEqual(explicit_empty.stdout, omitted.stdout)
        document["database"]["Mounts"].pop()
        self.assertNotEqual(self._identity(document).stdout, omitted.stdout)

    def test_identity_requires_sources_for_bind_and_volume_mounts(self) -> None:
        for mount_type in ("bind", "volume"):
            for source in (None, ""):
                with self.subTest(mount_type=mount_type, source=source):
                    document = _deployment_identity_document()
                    mount = document["database"]["Mounts"][0]
                    mount["Type"] = mount_type
                    if source is None:
                        del mount["Source"]
                    else:
                        mount["Source"] = source
                    result = self._identity(document)
                    self.assertEqual(result.returncode, 4)

    def test_identity_rejects_malformed_inspection_without_echoing_values(self) -> None:
        for field, value in (("Id", None), ("Mounts", "synthetic-secret"), ("Mounts", [{}])):
            with self.subTest(field=field, value=value):
                document = _deployment_identity_document()
                document["database"][field] = value
                result = self._identity(document)
                self.assertEqual(result.returncode, 4)
                self.assertNotIn("synthetic-secret", result.stdout + result.stderr)

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

    def test_split_roles_validate_runtime_and_migration_credentials_independently(self) -> None:
        document = _compose_document()
        database_environment = document["services"]["db"]["environment"]
        database_environment.update({
            "POSTGRES_RUNTIME_USER": "runtime",
            "POSTGRES_RUNTIME_PASSWORD": "runtime-secret",
            "POSTGRES_MIGRATION_USER": "migration",
            "POSTGRES_MIGRATION_PASSWORD": "migration-secret",
        })
        for name in ("api", "worker"):
            document["services"][name]["environment"]["DATABASE_URL"] = (
                "postgresql+psycopg://runtime:runtime-secret@db/threatlens"
            )
        document["services"]["migrate"] = {
            "environment": {"DATABASE_URL": "postgresql+psycopg://migration:migration-secret@db/threatlens"},
            "networks": {"backplane": None},
        }
        result = self._run("validate-target", input_text=json.dumps(document))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[4], "api,migrate,worker")
        self.assertNotIn("secret", result.stdout)

        document["services"]["api"]["environment"]["DATABASE_URL"] = (
            "postgresql+psycopg://migration:migration-secret@db/threatlens"
        )
        result = self._run("validate-target", input_text=json.dumps(document))
        self.assertEqual(result.returncode, 4)
        self.assertIn("user differs", result.stderr)
        self.assertNotIn("secret", result.stderr)

    def test_split_roles_refuse_incomplete_or_privilege_leaking_configuration(self) -> None:
        document = _compose_document()
        document["services"]["db"]["environment"]["POSTGRES_RUNTIME_USER"] = "runtime"
        result = self._run("validate-target", input_text=json.dumps(document))
        self.assertEqual(result.returncode, 4)
        self.assertIn("POSTGRES_RUNTIME_PASSWORD", result.stderr)

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
