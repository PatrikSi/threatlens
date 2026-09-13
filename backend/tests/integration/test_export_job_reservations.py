import json
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.export_job import ExportJob, ExportJobChunk
from app.services import export_job_worker as worker
from tests.integration.test_export_jobs import _accept, _job, export_env as export_env


def test_ready_jobs_release_unused_capacity_and_fence_further_chunks(export_env, monkeypatch, tmp_path):
    env = export_env
    settings = get_settings()
    # Allow one peak generation reservation plus several small retained files.
    monkeypatch.setattr(settings, "export_job_max_reserved_bytes",
                        settings.export_max_uncompressed_bytes * 3 + settings.export_max_items * 256 + 100_000)
    captured = {}
    publish = worker._publish

    def capture_claim(job_id, token, artifact):
        captured[job_id] = token
        return publish(job_id, token, artifact)

    monkeypatch.setattr(worker, "_publish", capture_claim)
    first, _ = _accept(env)
    peak_reservation = _job(env, first).reserved_bytes
    blocked = env.client.post("/exports/jobs", json={"format": "csv", "idempotency_key": str(uuid.uuid4())}, headers=env.headers)
    assert blocked.status_code == 429
    assert worker.execute_export_job(first)["status"] == "ready"
    with Session(env.engine) as db:
        job = db.get(ExportJob, first)
        chunks = db.scalars(select(ExportJobChunk.ciphertext).where(ExportJobChunk.job_id == first)).all()
        metadata = (job.request_encrypted, job.authorization_encrypted, job.source_encrypted)
        expected = (16_384 + sum(len(json.dumps(value, separators=(",", ":")).encode()) for value in metadata)
                    + sum(len(value.encode()) + 128 for value in chunks))
        assert job.source_encrypted is not None
        assert job.reserved_bytes == expected
        assert job.reserved_bytes < peak_reservation // 100

    # Publication cleared the token before making released capacity available.
    path = tmp_path / "late-chunk"
    path.write_bytes(b"must never be appended")
    with pytest.raises(worker.ExportJobInterrupted):
        worker._store_artifact(first, captured[first], SimpleNamespace(path=path, file_size=path.stat().st_size), lambda **_kwargs: None)
    for _ in range(2):
        later, _ = _accept(env)
        assert worker.execute_export_job(later)["status"] == "ready"
    with Session(env.engine) as db:
        assert db.scalar(select(func.count()).select_from(ExportJobChunk).where(ExportJobChunk.job_id == first)) == len(chunks)
        assert db.scalar(select(func.sum(ExportJob.reserved_bytes))) < settings.export_job_max_reserved_bytes
