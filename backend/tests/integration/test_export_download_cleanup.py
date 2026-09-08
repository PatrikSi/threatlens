import os
import tempfile
from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import Session

from app.api.routes import export_jobs as export_routes
from app.models.api_token import ApiToken
from app.models.export_job import ExportJob
from app.services import export_job_download
from app.services.export_download_scratch import ExportDownloadScratch
from tests.integration import test_export_jobs as export_fixtures
from tests.integration.test_export_jobs import _accept, worker

export_env = export_fixtures.export_env


@pytest.fixture
def scratch_owners(tmp_path, monkeypatch):
    # Neither rendering recovery nor downloads may inspect the host's live tmp.
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    owners = []

    def create():
        owner = ExportDownloadScratch()
        assert os.fstat(owner.file.fileno()).st_nlink == 0
        owners.append(owner)
        return owner

    monkeypatch.setattr(export_job_download, "ExportDownloadScratch", create)
    yield owners
    assert owners and all(owner.file.closed for owner in owners)
    assert list(tmp_path.glob("threatlens-export-download-*")) == []


def test_real_job_download_closes_anonymous_plaintext_and_keeps_range_support(export_env, scratch_owners):
    job_id, _ = _accept(export_env)
    assert worker.execute_export_job(job_id)["status"] == "ready"
    url = f"/exports/jobs/{job_id}/download"
    complete = export_env.client.get(url, headers=export_env.headers)
    assert complete.status_code == 200, complete.text
    assert "Private export summary" in complete.text
    assert complete.headers["cache-control"] == "no-store"
    partial = export_env.client.get(url, headers={**export_env.headers, "Range": "bytes=0-19"})
    assert partial.status_code == 206, partial.text
    assert partial.content == complete.content[:20]
    assert len(scratch_owners) == 2


def test_access_revocation_during_materialization_closes_plaintext(export_env, scratch_owners, monkeypatch):
    job_id, _ = _accept(export_env)
    assert worker.execute_export_job(job_id)["status"] == "ready"
    decrypt = export_job_download.decrypt_text

    def revoke_then_decode(value):
        with Session(export_env.engine) as db:
            db.get(ApiToken, export_env.credential_id).revoked_at = datetime.now(timezone.utc)
            db.commit()
        return decrypt(value)

    monkeypatch.setattr(export_job_download, "decrypt_text", revoke_then_decode)
    response = export_env.client.get(f"/exports/jobs/{job_id}/download", headers=export_env.headers)
    assert response.status_code == 409
    assert "Private export summary" not in response.text


def test_incomplete_stored_artifact_closes_plaintext_without_serving_it(export_env, scratch_owners):
    job_id, _ = _accept(export_env)
    assert worker.execute_export_job(job_id)["status"] == "ready"
    with Session(export_env.engine) as db:
        db.get(ExportJob, job_id).file_size += 1
        db.commit()
    response = export_env.client.get(f"/exports/jobs/{job_id}/download", headers=export_env.headers)
    assert response.status_code == 410
    assert "Private export summary" not in response.text


def test_audit_failure_after_materialization_closes_the_owned_descriptor(export_env, scratch_owners, monkeypatch):
    job_id, _ = _accept(export_env)
    assert worker.execute_export_job(job_id)["status"] == "ready"

    def unavailable_audit(*_args, **_kwargs):
        assert scratch_owners and not scratch_owners[0].file.closed
        raise RuntimeError("audit persistence unavailable")

    monkeypatch.setattr(export_routes, "record_audit", unavailable_audit)
    with pytest.raises(RuntimeError, match="audit persistence unavailable"):
        export_env.client.get(f"/exports/jobs/{job_id}/download", headers=export_env.headers)
