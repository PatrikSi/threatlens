"""Deterministic preparation failures with small durable artifacts."""

import base64
import tempfile

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.export_job import ExportJobChunk
from app.services import export_download_budget, export_job_download
from app.services.export_job_worker import execute_export_job
from app.services.secret_storage import decrypt_text, encrypt_text
from tests.integration.test_export_jobs import _accept, _job, export_env as export_env


def test_storage_admission_failure_is_retriable_without_consuming_the_artifact(export_env, monkeypatch):
    env = export_env
    job_id, _ = _accept(env)
    assert execute_export_job(job_id)["status"] == "ready"
    settings = get_settings()
    original = settings.export_download_scratch_headroom_bytes
    monkeypatch.setattr(settings, "export_download_scratch_headroom_bytes", 1 << 60)
    response = env.client.get(f"/exports/jobs/{job_id}/download", headers=env.headers)
    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "export_download_capacity"
    assert response.headers["retry-after"] == "5"
    assert _job(env, job_id).status == "ready"
    monkeypatch.setattr(settings, "export_download_scratch_headroom_bytes", original)
    assert env.client.get(f"/exports/jobs/{job_id}/download", headers=env.headers).status_code == 200


def test_cumulative_decryption_deadline_closes_partial_plaintext_and_can_retry(export_env, monkeypatch, tmp_path):
    env = export_env
    job_id, _ = _accept(env)
    assert execute_export_job(job_id)["status"] == "ready"
    with Session(env.engine) as db:
        first = db.scalar(select(ExportJobChunk).where(ExportJobChunk.job_id == job_id))
        plaintext = base64.b64decode(decrypt_text(first.ciphertext))
        midpoint = len(plaintext) // 2
        first.ciphertext = encrypt_text(base64.b64encode(plaintext[:midpoint]).decode("ascii"))
        db.add(ExportJobChunk(job_id=job_id, position=1, ciphertext=encrypt_text(base64.b64encode(plaintext[midpoint:]).decode("ascii"))))
        db.commit()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    clock = [0.0]
    monkeypatch.setattr(export_download_budget, "monotonic", lambda: clock[0])
    monkeypatch.setattr(get_settings(), "export_download_preparation_timeout_seconds", 30)
    scratches = []
    scratch_type = export_job_download.ExportDownloadScratch

    def scratch(**kwargs):
        value = scratch_type(**kwargs)
        scratches.append(value)
        return value

    def advancing_decrypt(ciphertext):
        result = decrypt_text(ciphertext)
        clock[0] += 16.0
        return result

    monkeypatch.setattr(export_job_download, "ExportDownloadScratch", scratch)
    monkeypatch.setattr(export_job_download, "decrypt_text", advancing_decrypt)
    response = env.client.get(f"/exports/jobs/{job_id}/download", headers=env.headers)
    assert response.status_code == 504, response.text
    assert response.json()["error"]["code"] == "export_download_preparation_deadline"
    assert response.headers["retry-after"] == "5"
    assert scratches and all(value.file.closed for value in scratches)
    assert _job(env, job_id).status == "ready"
    monkeypatch.setattr(export_job_download, "decrypt_text", decrypt_text)
    recovered = env.client.get(f"/exports/jobs/{job_id}/download", headers=env.headers)
    assert recovered.status_code == 200, recovered.text
    assert recovered.content == plaintext
    assert all(value.file.closed for value in scratches)
