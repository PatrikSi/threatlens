import base64
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import generate_api_token
from app.db.session import get_db
from app.main import app
from app.models.api_token import ApiToken
from app.models.article import Article
from app.models.data_policy import DataPolicyState, QUARANTINE_HANDLING_LABEL_ID, UNRESTRICTED_HANDLING_LABEL_ID
from app.models.export_job import ExportJob, ExportJobChunk
from app.models.feed import Feed
from app.models.user import User
from app.services import export_job_worker as worker
from app.services import export_job_download
from app.services.export_jobs import maintain_export_jobs
from app.services.feed_pipeline import upsert_item_from_parsed
from app.services.secret_storage import encrypt_text
from app.tasks import export_tasks


@pytest.fixture
def export_env(database_engine, monkeypatch, _install_test_redis_backend):
    def sessions():
        with Session(database_engine) as db:
            yield db

    app.dependency_overrides[get_db] = sessions

    @contextmanager
    def unlocked(**_kwargs):
        yield

    monkeypatch.setattr(worker, "acquire_export_lock", unlocked)
    monkeypatch.setattr(export_tasks.generate_export_job, "apply_async", lambda **_kwargs: None)
    with Session(database_engine) as db:
        key = uuid.uuid4().hex
        owner = User(email=f"export-{key}@example.invalid", password_hash="unused", role="viewer", is_active=True, is_approved=True)
        db.add(owner)
        db.flush()
        raw_token, prefix, digest = generate_api_token()
        credential = ApiToken(user_id=owner.id, name="Export test", token_prefix=prefix, token_hash=digest, scopes=["read:items"], expires_at=datetime.now(timezone.utc) + timedelta(days=1))
        db.add(credential)
        feed = Feed(name="Export feed", url=f"https://example.invalid/{key}", handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID)
        db.add(feed)
        db.flush()
        item, _, _ = upsert_item_from_parsed(db, feed, SimpleNamespace(
            url=f"https://example.invalid/article/{key}", guid=key, title="Export source evidence",
            summary="Private export summary", published_at=None,
        ))
        db.add(Article(item_id=item.id, final_url=item.url, http_status=200, text="Private article body"))
        state = db.get(DataPolicyState, 1)
        policy_before = state.mode, state.coverage_version, state.revision, state.enforced_at, state.enforced_by_user_id
        db.commit()
        ids = owner.id, credential.id, feed.id, item.id
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, headers={"Authorization": f"Bearer {raw_token}"},
                              owner_id=ids[0], credential_id=ids[1], feed_id=ids[2], item_id=ids[3], engine=database_engine)
    app.dependency_overrides.pop(get_db, None)
    with Session(database_engine) as db:
        state = db.get(DataPolicyState, 1)
        state.mode, state.coverage_version, state.revision, state.enforced_at, state.enforced_by_user_id = policy_before
        db.flush()
        db.execute(delete(ExportJob).where(ExportJob.principal_id == ids[0]))
        db.execute(delete(Feed).where(Feed.id == ids[2]))
        db.execute(delete(User).where(User.id == ids[0]))
        db.commit()


def _accept(env, **updates):
    payload = {"format": "jsonl", "filters": {"feed_ids": [str(env.feed_id)]}, "idempotency_key": str(uuid.uuid4()), **updates}
    response = env.client.post("/exports/jobs", json=payload, headers=env.headers)
    assert response.status_code == 202, response.text
    return uuid.UUID(response.json()["id"]), payload


def _job(env, job_id):
    with Session(env.engine) as db:
        job = db.get(ExportJob, job_id)
        db.expunge(job)
        return job


def test_acceptance_survives_broker_failure_and_idempotent_retry(export_env, monkeypatch):
    env = export_env
    monkeypatch.setattr(export_tasks.generate_export_job, "apply_async", lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("broker down")))
    job_id, payload = _accept(env)
    again = env.client.post("/exports/jobs", json=payload, headers=env.headers)
    assert again.status_code == 202
    assert again.json()["id"] == str(job_id)
    assert _job(env, job_id).status == "queued"
    conflict = env.client.post("/exports/jobs", json={**payload, "format": "csv"}, headers=env.headers)
    assert conflict.status_code == 409
    dispatched = []
    monkeypatch.setattr(export_tasks.generate_export_job, "apply_async", lambda **kwargs: dispatched.append(kwargs))
    result = export_tasks.dispatch_export_jobs()
    assert result["queued"] == 1
    assert dispatched[0]["args"] == [str(job_id)]
    assert worker.execute_export_job(job_id)["status"] == "ready"


def test_job_generates_encrypted_artifact_and_downloads_on_a_later_request(export_env):
    env = export_env
    job_id, _ = _accept(env)
    assert worker.execute_export_job(job_id)["status"] == "ready"
    assert worker.execute_export_job(job_id)["status"] == "skipped"
    listing = env.client.get("/exports/jobs", headers=env.headers)
    assert listing.status_code == 200
    assert listing.json()["items"][0]["download_available"]
    with Session(env.engine) as db:
        chunk = db.scalar(select(ExportJobChunk.ciphertext).where(ExportJobChunk.job_id == job_id))
        assert chunk.startswith("enc:v1:")
        assert "Private article body" not in chunk
    response = env.client.get(f"/exports/jobs/{job_id}/download", headers=env.headers)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert "Private article body" in response.text
    assert len(response.content) == _job(env, job_id).file_size


def test_original_credential_revocation_fails_execution_and_redacts_status(export_env):
    env = export_env
    job_id, _ = _accept(env)
    with Session(env.engine) as db:
        credential = db.get(ApiToken, env.credential_id)
        credential.revoked_at = datetime.now(timezone.utc)
        raw, prefix, digest = generate_api_token()
        db.add(ApiToken(user_id=env.owner_id, name="Replacement", token_prefix=prefix, token_hash=digest, scopes=["read:items"]))
        db.commit()
    assert worker.execute_export_job(job_id)["status"] == "failed"
    response = env.client.get(f"/exports/jobs/{job_id}", headers={"Authorization": f"Bearer {raw}"})
    assert response.status_code == 200
    assert response.json()["error_code"] == "authorization_changed"
    assert response.json()["filename"] is None
    assert response.json()["item_count"] is None
    assert not response.json()["download_available"]


def test_handling_label_revocation_blocks_download_and_status_details(export_env):
    env = export_env
    job_id, _ = _accept(env)
    assert worker.execute_export_job(job_id)["status"] == "ready"
    with Session(env.engine) as db:
        state = db.get(DataPolicyState, 1)
        state.mode = "enforced"
        state.coverage_version = 1
        state.revision += 1
        state.enforced_at = datetime.now(timezone.utc)
        state.enforced_by_user_id = env.owner_id
        db.get(Feed, env.feed_id).handling_label_id = QUARANTINE_HANDLING_LABEL_ID
        db.commit()
    response = env.client.get(f"/exports/jobs/{job_id}/download", headers=env.headers)
    assert response.status_code == 409, response.text
    response = env.client.get("/exports/jobs", headers=env.headers)
    assert response.status_code == 200, response.text
    assert not response.json()["items"][0]["download_available"]
    assert response.json()["items"][0]["file_size"] is None


def test_cancelled_worker_cannot_publish_and_partial_chunks_are_removed(export_env, monkeypatch):
    env = export_env
    job_id, _ = _accept(env)
    original = worker._publish

    def cancel_then_publish(*args):
        response = env.client.post(f"/exports/jobs/{job_id}/cancel", headers=env.headers)
        assert response.status_code == 200
        return original(*args)

    monkeypatch.setattr(worker, "_publish", cancel_then_publish)
    assert worker.execute_export_job(job_id)["status"] == "interrupted"
    assert _job(env, job_id).status == "cancelled"
    with Session(env.engine) as db:
        assert db.scalar(select(func.count()).select_from(ExportJobChunk).where(ExportJobChunk.job_id == job_id)) == 0


def test_expired_claim_is_repaired_and_old_worker_is_fenced(export_env):
    env = export_env
    job_id, _ = _accept(env)
    token = worker.claim_export_job(job_id)
    assert token
    assert worker.claim_export_job(job_id) is None
    with Session(env.engine) as db:
        job = db.get(ExportJob, job_id)
        job.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.add(ExportJobChunk(job_id=job_id, position=0, ciphertext=encrypt_text(base64.b64encode(b"partial").decode())))
        db.commit()
        assert maintain_export_jobs(db) == 1
        db.commit()
    with Session(env.engine) as db:
        with pytest.raises(worker.ExportJobInterrupted):
            worker._owned_job(db, job_id, token)
    assert worker.execute_export_job(job_id)["status"] == "ready"
    assert _job(env, job_id).attempts == 2


def test_storage_admission_expiry_and_deleted_owner_cleanup(export_env, monkeypatch):
    env = export_env
    monkeypatch.setattr(get_settings(), "export_job_max_active_per_principal", 1)
    job_id, _ = _accept(env)
    response = env.client.post("/exports/jobs", json={"format": "csv", "idempotency_key": str(uuid.uuid4())}, headers=env.headers)
    assert response.status_code == 429, response.text
    assert worker.execute_export_job(job_id)["status"] == "ready"
    with Session(env.engine) as db:
        db.get(ExportJob, job_id).expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
        maintain_export_jobs(db)
        db.commit()
        assert db.get(ExportJob, job_id).reserved_bytes == 0
        assert db.scalar(select(func.count()).select_from(ExportJobChunk)) == 0
    assert env.client.get(f"/exports/jobs/{job_id}/download", headers=env.headers).status_code == 410
    second, _ = _accept(env)
    with Session(env.engine) as db:
        db.execute(delete(User).where(User.id == env.owner_id))
        db.commit()
        maintain_export_jobs(db)
        db.commit()
        assert db.get(ExportJob, second) is None


def test_total_generation_deadline_exceeds_five_minutes_but_is_bounded(export_env, monkeypatch):
    env = export_env
    job_id, _ = _accept(env)
    token = worker.claim_export_job(job_id)
    checkpoint = worker._checkpoint(job_id, token, 0, threading.Event())
    monkeypatch.setattr(worker.time, "monotonic", lambda: 301)
    checkpoint(force=True)
    monkeypatch.setattr(worker.time, "monotonic", lambda: get_settings().export_job_timeout_seconds + 1)
    with pytest.raises(worker.ExportJobTimedOut):
        checkpoint(force=True)


def test_revocation_while_download_is_prepared_never_sends_artifact(export_env, monkeypatch):
    env = export_env
    job_id, _ = _accept(env)
    assert worker.execute_export_job(job_id)["status"] == "ready"
    decrypt = export_job_download.decrypt_text

    def revoke_then_decode(value):
        with Session(env.engine) as db:
            db.get(ApiToken, env.credential_id).revoked_at = datetime.now(timezone.utc)
            db.commit()
        return decrypt(value)

    monkeypatch.setattr(export_job_download, "decrypt_text", revoke_then_decode)
    response = env.client.get(f"/exports/jobs/{job_id}/download", headers=env.headers)
    assert response.status_code == 409
    assert "Private article body" not in response.text


def test_access_change_after_render_before_publication_removes_chunks(export_env, monkeypatch):
    env = export_env
    job_id, _ = _accept(env)
    publish = worker._publish

    def revoke_then_publish(*args):
        with Session(env.engine) as db:
            db.get(User, env.owner_id).is_active = False
            db.commit()
        return publish(*args)

    monkeypatch.setattr(worker, "_publish", revoke_then_publish)
    assert worker.execute_export_job(job_id)["status"] == "failed"
    assert _job(env, job_id).error_code == "authorization_changed"
    with Session(env.engine) as db:
        assert db.scalar(select(func.count()).select_from(ExportJobChunk).where(ExportJobChunk.job_id == job_id)) == 0


def test_hard_exit_after_storing_partial_artifact_is_repaired(export_env, monkeypatch):
    env = export_env
    job_id, _ = _accept(env)
    publish = worker._publish
    monkeypatch.setattr(worker, "_publish", lambda *_args: (_ for _ in ()).throw(SystemExit("worker killed")))
    with pytest.raises(SystemExit):
        worker.execute_export_job(job_id)
    with Session(env.engine) as db:
        job = db.get(ExportJob, job_id)
        assert job.status == "running"
        assert db.scalar(select(func.count()).select_from(ExportJobChunk).where(ExportJobChunk.job_id == job_id)) > 0
        job.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
        maintain_export_jobs(db)
        db.commit()
    monkeypatch.setattr(worker, "_publish", publish)
    assert worker.execute_export_job(job_id)["status"] == "ready"
    assert _job(env, job_id).attempts == 2
