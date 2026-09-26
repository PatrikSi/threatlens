"""Opt-in reconstruction after deleting every original disposable data volume."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import sys
import subprocess
import tempfile
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts/operations"))
from monitor import atomic_json  # noqa: E402
from record_evidence import key_probe, verify_key_probe  # noqa: E402
from tests.recovery import test_recovery_docker_e2e as recovery_fixture  # noqa: E402


@unittest.skipUnless(os.environ.get("THREATLENS_RUN_HOST_LOSS_QUALIFICATION") == "1",
                     "opt-in disposable source-loss reconstruction")
class HostLossQualificationTests(unittest.TestCase):
    def test_reconstruct_from_independent_archive_and_key_after_source_removal(self):
        image = os.environ.get("RECOVERY_E2E_BACKEND_IMAGE")
        if not image:
            self.fail("An explicit locally built current-source RECOVERY_E2E_BACKEND_IMAGE is required")
        helper = recovery_fixture.RecoveryDockerEndToEndTests("test_backup_drill_and_destructive_restore_preserve_invariants")
        original_compose = recovery_fixture.COMPOSE_FILE
        result = {"schema_version": 1, "kind": "host_loss_qualification", "status": "failed",
            "deployment": os.environ.get("THREATLENS_HOST_LOSS_DEPLOYMENT", "local-qualification"),
            "run_id": uuid.uuid4().hex, "scope": "disposable_local_fault_domain_simulation",
            "production_qualified": False, "started_at": datetime.now(timezone.utc).isoformat(),
            "source_environment_removed": False, "restore_verified": False,
            "outbound_quarantined": False, "independent_key_used": False}
        output = Path(os.environ.get("THREATLENS_HOST_LOSS_OUTPUT", "/tmp/threatlens-host-loss-qualification.json"))
        helper.setUp()
        try:
            with tempfile.TemporaryDirectory(prefix="threatlens-independent-recovery-") as independent:
                isolated = Path(independent)
                # Absolute source-owned provisioning paths keep the test Compose
                # file relocatable; its project is a fresh random test identity.
                compose = helper.root / "compose.yml"
                compose.write_text(original_compose.read_text().replace(
                    "../../scripts/database/provision-roles.sh", str(ROOT / "scripts/database/provision-roles.sh")))
                recovery_fixture.COMPOSE_FILE = compose
                image_id = subprocess.check_output(["docker", "image", "inspect", "--format", "{{.Id}}", image],
                    text=True, timeout=30).strip()
                helper.environment["RECOVERY_E2E_BACKEND_IMAGE"] = image_id
                result["backend_image_id"] = image_id
                digest_script = """
from pathlib import Path
import hashlib
value = hashlib.sha256()
files = [Path('alembic.ini')]
for name in ('app', 'alembic'):
    files.extend(path for path in Path(name).rglob('*') if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc')
for path in sorted(files):
    value.update(str(path).encode() + b'\\0' + path.read_bytes())
print(value.hexdigest())
"""
                expected_source = subprocess.check_output([sys.executable, "-c", digest_script],
                    cwd=ROOT / "backend", text=True, timeout=30).strip()
                actual_source = helper._compose("run", "--rm", "--no-deps", "api", "python", "-c", digest_script).stdout.strip()
                self.assertEqual(expected_source, actual_source, "The supplied image must contain the exact current backend source")
                result["backend_source_sha256"] = actual_source
                helper._compose("up", "--detach", "--wait", "db", "redis")
                helper._compose("run", "--rm", "--no-deps", "migrate")
                result["migration_head"] = helper._psql("SELECT version_num FROM alembic_version;")
                helper._psql("SET ROLE threatlens_migration; CREATE TABLE qualification_secret (ciphertext text NOT NULL);")
                helper._compose("run", "--rm", "--no-deps", "api", "python", "-c", """
from sqlalchemy import text
from app.db.session import SessionLocal
from app.services.secret_storage import encrypt_text
from app.models.feed import Feed
with SessionLocal.begin() as db:
    db.add(Feed(name="Reconstructed synthetic feed", url="https://recovery.example.com/feed", enabled=True))
    db.execute(text('INSERT INTO qualification_secret VALUES (:value)'), {'value': encrypt_text('independent-recovery-marker')})
""")
                # The key probe and copied key leave the source environment before
                # it is destroyed. The probe contains no plaintext application key.
                escrow = isolated / "recovered.key"
                escrow.write_text(helper.environment["APP_DATA_ENCRYPTION_KEY"])
                escrow.chmod(0o600)
                probe = key_probe(escrow.read_bytes())
                snapshot = helper._recovery("backup", "--output-dir", str(helper.backup_directory)).stdout.strip()
                original_manifest = json.loads((Path(snapshot) / "manifest.json").read_text())
                result["archive_sha256"] = original_manifest["archive"]["sha256"]
                copied = isolated / "backup"
                shutil.copytree(snapshot, copied)
                helper._recovery("verify", "--backup", str(copied))
                original_volume = helper._compose("ps", "--quiet", "db").stdout.strip()
                helper._compose("down", "--volumes", "--remove-orphans")
                shutil.rmtree(helper.backup_directory)
                if (helper.root / "journal").exists():
                    shutil.rmtree(helper.root / "journal")
                helper.env_file.unlink()
                # Discard every original connection/secret value; reconstruction
                # uses new database/broker/JWT/admin credentials and the escrow key.
                for name in list(helper.environment):
                    if name.startswith("RECOVERY_E2E_") or name == "APP_DATA_ENCRYPTION_KEY":
                        helper.environment.pop(name)
                recovered_key = escrow.read_text()
                verify_key_probe(probe, recovered_key.encode())
                values = {"APP_DATA_ENCRYPTION_KEY": recovered_key, "RECOVERY_E2E_ENCRYPTION_KEY": recovered_key,
                    "RECOVERY_E2E_ADMIN_PASSWORD": secrets.token_hex(24), "RECOVERY_E2E_JWT_SECRET": secrets.token_urlsafe(48),
                    "RECOVERY_E2E_POSTGRES_PASSWORD": secrets.token_hex(24), "RECOVERY_E2E_RUNTIME_PASSWORD": secrets.token_hex(24),
                    "RECOVERY_E2E_MIGRATION_PASSWORD": secrets.token_hex(24), "RECOVERY_E2E_REDIS_PASSWORD": secrets.token_hex(24)}
                helper.environment.update(values, RECOVERY_E2E_BACKEND_IMAGE=image_id)
                helper.env_file.write_text("".join(f"{key}={value}\n" for key, value in sorted(values.items())))
                helper.env_file.chmod(0o600)
                helper._compose("up", "--detach", "--wait", "db", "redis")
                replacement = helper._compose("ps", "--quiet", "db").stdout.strip()
                self.assertNotEqual(original_volume, replacement)
                result["source_environment_removed"] = True
                helper._compose("run", "--rm", "--no-deps", "migrate")
                confirmation = helper._recovery("restore", "--backup", str(copied), "--show-confirmation").stdout.strip()
                helper._recovery("restore", "--backup", str(copied), "--confirm", confirmation,
                    "--acknowledge-data-loss", "--safety-backup-dir", str(isolated / "new-target-safety"))
                verified = helper._compose("run", "--rm", "--no-deps", "api", "python", "-c", """
from sqlalchemy import text
from app.db.session import SessionLocal
from app.services.secret_storage import decrypt_text
with SessionLocal() as db:
    value = db.scalar(text('SELECT ciphertext FROM qualification_secret'))
    assert decrypt_text(value) == 'independent-recovery-marker'
print('independent recovered key decrypted the restored database')
""")
                self.assertIn("independent recovered key", verified.stdout)
                self.assertEqual(helper._psql("SELECT count(*) FROM feeds WHERE enabled;"), "0")
                self.assertEqual(helper._psql("SELECT has_schema_privilege('threatlens_runtime','public','CREATE')::text;"), "false")
                self.assertEqual(hashlib.sha256((copied / "database.dump").read_bytes()).hexdigest(), result["archive_sha256"])
                result.update(status="passed", restore_verified=True, outbound_quarantined=True, independent_key_used=True)
        finally:
            try:
                helper.tearDown()
            finally:
                recovery_fixture.COMPOSE_FILE = original_compose
                result["finished_at"] = datetime.now(timezone.utc).isoformat()
                atomic_json(output, result)


if __name__ == "__main__":
    unittest.main()
