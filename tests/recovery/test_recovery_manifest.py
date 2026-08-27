from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
HELPER = REPOSITORY_ROOT / "scripts" / "recovery" / "recovery_manifest.py"


class RecoveryManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _run(
        self, *arguments: str, check: bool = False
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(HELPER), *arguments],
            check=check,
            capture_output=True,
            text=True,
        )

    def _create_backup(
        self, *, directory_name: str = "threatlens-postgresql-test"
    ) -> Path:
        backup = self.root / directory_name
        backup.mkdir(mode=0o700)
        archive = backup / "database.dump"
        archive.write_bytes(b"PGDMP\x01test-archive-content")
        os.chmod(archive, 0o600)
        result = self._run(
            "create",
            "--archive",
            str(archive),
            "--output",
            str(backup / "manifest.json"),
            "--app-version",
            "1.7.0",
            "--alembic-revision",
            "0056_report_task_lineage",
            "--postgresql-version",
            "16.10 (Debian 16.10-1)",
            "--snapshot-time-utc",
            "2026-08-27T12:00:00Z",
            "--metadata-collected-at-utc",
            "2026-08-27T12:01:00Z",
            "--database-size-bytes",
            "4096",
            "--estimated-counts-json",
            '{"feeds": 2, "items": 12, "users": 1}',
            "--encryption-key-fingerprint",
            "sha256:0123456789abcdef0123456789abcdef",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return backup

    def _manifest(self, backup: Path) -> dict:
        return json.loads((backup / "manifest.json").read_text(encoding="utf-8"))

    def _write_manifest(self, backup: Path, document: dict) -> None:
        (backup / "manifest.json").write_text(
            json.dumps(document),
            encoding="utf-8",
        )

    def test_create_is_atomic_and_restrictive(self) -> None:
        backup = self._create_backup()

        manifest = backup / "manifest.json"
        self.assertEqual(manifest.stat().st_mode & 0o777, 0o600)
        self.assertFalse(list(backup.glob("*.partial")))
        result = self._run("verify", "--backup", str(backup))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_inspect_returns_all_bounded_fields_after_one_validation(self) -> None:
        backup = self._create_backup()

        result = self._run(
            "inspect", "--backup", str(backup), "--expected-app-version", "1.7.0"
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        fields = result.stdout.splitlines()
        self.assertEqual(len(fields), 8)
        self.assertEqual(Path(fields[0]), (backup / "manifest.json").absolute())
        self.assertEqual(Path(fields[1]), (backup / "database.dump").absolute())
        self.assertEqual(fields[2], "1.7.0")
        self.assertEqual(fields[3], "0056_report_task_lineage")
        self.assertRegex(fields[4], r"^[0-9a-f]{64}$")
        self.assertEqual(int(fields[5]), (backup / "database.dump").stat().st_size)
        self.assertEqual(fields[6], "16.10 (Debian 16.10-1)")
        self.assertEqual(fields[7], "sha256:0123456789abcdef0123456789abcdef")

    def test_malformed_manifest_is_rejected(self) -> None:
        backup = self._create_backup()
        (backup / "manifest.json").write_text("{not-json", encoding="utf-8")

        result = self._run("verify", "--backup", str(backup))

        self.assertEqual(result.returncode, 4)
        self.assertIn("not valid UTF-8 JSON", result.stderr)

    def test_traversal_archive_name_is_rejected(self) -> None:
        backup = self._create_backup()
        manifest = self._manifest(backup)
        manifest["archive"]["filename"] = "../database.dump"
        self._write_manifest(backup, manifest)

        result = self._run("verify", "--backup", str(backup))

        self.assertEqual(result.returncode, 4)
        self.assertIn("archive.filename", result.stderr)

    def test_symlink_archive_is_rejected(self) -> None:
        backup = self._create_backup()
        archive = backup / "database.dump"
        target = self.root / "outside.dump"
        target.write_bytes(archive.read_bytes())
        archive.unlink()
        archive.symlink_to(target)

        result = self._run("verify", "--backup", str(backup))

        self.assertEqual(result.returncode, 4)
        self.assertIn("Symbolic links are not allowed", result.stderr)

    def test_archive_size_mismatch_is_rejected(self) -> None:
        backup = self._create_backup()
        with (backup / "database.dump").open("ab") as archive:
            archive.write(b"extra")

        result = self._run("verify", "--backup", str(backup))

        self.assertEqual(result.returncode, 4)
        self.assertIn("Archive size does not match", result.stderr)

    def test_bad_checksum_is_rejected(self) -> None:
        backup = self._create_backup()
        manifest = self._manifest(backup)
        manifest["archive"]["sha256"] = "0" * 64
        self._write_manifest(backup, manifest)

        result = self._run("verify", "--backup", str(backup))

        self.assertEqual(result.returncode, 4)
        self.assertIn("SHA-256 does not match", result.stderr)

    def test_wrong_manifest_version_is_rejected(self) -> None:
        backup = self._create_backup()
        manifest = self._manifest(backup)
        manifest["schema_version"] = 99
        self._write_manifest(backup, manifest)

        result = self._run("verify", "--backup", str(backup))

        self.assertEqual(result.returncode, 4)
        self.assertIn("Unsupported manifest schema_version", result.stderr)

    def test_expected_application_version_mismatch_is_rejected(self) -> None:
        backup = self._create_backup()

        result = self._run(
            "verify",
            "--backup",
            str(backup),
            "--expected-app-version",
            "2.0.0",
        )

        self.assertEqual(result.returncode, 4)
        self.assertIn("Backup app version does not match", result.stderr)

    def test_interrupted_partial_directory_is_rejected(self) -> None:
        backup = self._create_backup(
            directory_name=".threatlens-backup.partial.abcd1234"
        )

        result = self._run("verify", "--backup", str(backup))

        self.assertEqual(result.returncode, 4)
        self.assertIn("Interrupted partial backup", result.stderr)

    def test_missing_archive_is_rejected(self) -> None:
        backup = self._create_backup()
        (backup / "database.dump").unlink()

        result = self._run("verify", "--backup", str(backup))

        self.assertEqual(result.returncode, 4)
        self.assertIn("archive does not exist", result.stderr)

    def test_fingerprint_never_prints_the_key(self) -> None:
        environment_file = self.root / ".env"
        secret = "this-is-a-long-random-encryption-key-value"
        environment_file.write_text(
            f"APP_DATA_ENCRYPTION_KEY={secret}\n",
            encoding="utf-8",
        )

        result = self._run("fingerprint-env", "--env-file", str(environment_file))

        expected = hashlib.sha256(secret.encode("utf-8")).hexdigest()[:32]
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), f"sha256:{expected}")
        self.assertNotIn(secret, result.stdout + result.stderr)

    def test_ledger_metadata_is_typed_bounded_and_allowlisted(self) -> None:
        result = self._run(
            "ledger-metadata",
            "--field",
            "tool_version=1",
            "--field",
            "archive_size_bytes=4096",
            "--field",
            "catalog_checked=true",
            "--field",
            "ledger_scope_id=" + "a" * 64,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "archive_size_bytes": 4096,
                "catalog_checked": True,
                "ledger_scope_id": "a" * 64,
                "tool_version": "1",
            },
        )

    def test_ledger_metadata_rejects_paths_and_sensitive_fields(self) -> None:
        result = self._run(
            "ledger-metadata",
            "--field",
            "backup_path=/srv/private/database.dump",
        )

        self.assertEqual(result.returncode, 4)
        self.assertIn("unsupported field", result.stderr)


if __name__ == "__main__":
    unittest.main()
