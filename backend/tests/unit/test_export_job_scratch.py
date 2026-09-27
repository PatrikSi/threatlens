"""Scratch cleanup must never infer authority over a different database."""
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.engine import make_url

from app.services import export_job_scratch as scratch


def database(url, jobs):
    db = MagicMock()
    db.__enter__.return_value = db
    db.get_bind.return_value.engine.url = make_url(url)
    db.get.side_effect = lambda _model, identity: jobs.get(identity)
    return db


def test_foreign_database_cleanup_preserves_active_export(monkeypatch, tmp_path):
    monkeypatch.setattr(scratch.tempfile, "gettempdir", lambda: str(tmp_path))
    job_id, token = uuid.uuid4(), uuid.uuid4()
    jobs = {job_id: SimpleNamespace(status="running", claim_token=token)}
    own = database("postgresql+psycopg://runtime:secret@localhost:5432/own", jobs)
    foreign = database("postgresql+psycopg://runtime:secret@localhost:5432/other", {})
    monkeypatch.setattr(scratch.session_module, "SessionLocal", lambda: own)
    path = scratch.export_scratch_directory(job_id, token)
    artifact = path / "partial.csv"
    artifact.write_text("private export")
    scratch.clean_local_export_scratch()
    assert artifact.exists()

    monkeypatch.setattr(scratch.session_module, "SessionLocal", lambda: foreign)
    scratch.clean_local_export_scratch()
    assert artifact.read_text() == "private export"
    assert foreign.get.call_count == 0

    jobs[job_id].status = "cancelled"
    monkeypatch.setattr(scratch.session_module, "SessionLocal", lambda: own)
    scratch.clean_local_export_scratch()
    assert not path.exists()


def test_rotated_password_reuses_namespace_without_exposing_credentials(monkeypatch, tmp_path):
    monkeypatch.setattr(scratch.tempfile, "gettempdir", lambda: str(tmp_path))
    first = database("postgresql+psycopg://runtime:first-secret@localhost/db?sslpassword=key-secret", {})
    rotated = database("postgresql+psycopg://runtime:second-secret@localhost/db?sslpassword=other-secret", {})
    assert scratch._scratch_root(first) == scratch._scratch_root(rotated)
    assert scratch._scratch_root(first) == scratch._scratch_root(database("postgresql+psycopg://runtime@localhost/db", {}))
    assert "secret" not in str(scratch._scratch_root(first))


@pytest.mark.parametrize("url", [
    "postgresql+psycopg://other@localhost/db",
    "postgresql+psycopg://runtime@another-host/db",
    "postgresql+psycopg://runtime@localhost:5433/db",
    "postgresql+psycopg://runtime@localhost/other",
    "postgresql+psycopg://runtime@localhost/db?host=/other/socket",
])
def test_connection_boundaries_have_distinct_namespaces(monkeypatch, tmp_path, url):
    monkeypatch.setattr(scratch.tempfile, "gettempdir", lambda: str(tmp_path))
    original = scratch._scratch_root(database("postgresql+psycopg://runtime@localhost/db", {}))
    assert scratch._scratch_root(database(url, {})) != original


def test_legacy_and_unrelated_directories_are_not_adopted(monkeypatch, tmp_path):
    monkeypatch.setattr(scratch.tempfile, "gettempdir", lambda: str(tmp_path))
    db = database("postgresql+psycopg://runtime@localhost/db", {})
    monkeypatch.setattr(scratch.session_module, "SessionLocal", lambda: db)
    legacy = tmp_path / f"{scratch.PREFIX}{uuid.uuid4()}-{uuid.uuid4()}"
    legacy.mkdir()
    (legacy / "in-flight.csv").write_text("older worker")
    root = scratch._scratch_root(db)
    (root / "unrecognized-directory").mkdir()
    (root / f"{scratch.PREFIX}malformed").mkdir()
    link = root / f"{scratch.PREFIX}{uuid.uuid4()}-{uuid.uuid4()}"
    link.symlink_to(legacy, target_is_directory=True)
    scratch.clean_local_export_scratch()
    assert (legacy / "in-flight.csv").exists()
    assert link.is_symlink()
    assert db.get.call_count == 0


def test_namespace_rejects_symlinks_and_public_permissions(monkeypatch, tmp_path):
    monkeypatch.setattr(scratch.tempfile, "gettempdir", lambda: str(tmp_path))
    db = database("postgresql+psycopg://runtime@localhost/db", {})
    root = scratch._scratch_root(db)
    root.chmod(0o755)
    with pytest.raises(RuntimeError, match="private directory"):
        scratch._scratch_root(db)
    root.rmdir()
    target = tmp_path / "foreign"
    target.mkdir(mode=0o700)
    root.symlink_to(target, target_is_directory=True)
    with pytest.raises(RuntimeError, match="private directory"):
        scratch._scratch_root(db)
