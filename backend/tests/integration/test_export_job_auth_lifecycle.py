import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.auth_session import AuthSession
from app.models.api_token import ApiToken
from app.models.export_job import ExportJob
from app.models.iam import IAMRole, IAMRolePermission
from app.models.service_account import ServiceAccount, ServiceAccountCredential, ServiceAccountRoleAssignment
from app.services.auth_sessions import create_auth_session
from app.services.export_job_scratch import clean_local_export_scratch, export_scratch_directory
from app.services.export_job_worker import ExportJobInterrupted, _owned_job, claim_export_job, execute_export_job
from app.services.service_accounts import _generate_service_account_token
from tests.integration.test_export_jobs import _accept, _job, export_env as export_env


def test_parallel_worker_claims_increment_attempts_once(export_env):
    env = export_env
    job_id, _ = _accept(env)
    start = threading.Barrier(2)

    def claim():
        start.wait(timeout=5)
        return claim_export_job(job_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _value: claim(), range(2)))
    assert len([token for token in results if token is not None]) == 1
    assert _job(env, job_id).attempts == 1


def test_parallel_admission_cannot_overcommit_global_artifact_reservation(export_env, monkeypatch):
    env = export_env
    settings = get_settings()
    monkeypatch.setattr(settings, "export_job_max_reserved_bytes", settings.export_max_uncompressed_bytes * 3 + settings.export_max_items * 256 + 100_000)
    start = threading.Barrier(2)

    def accept():
        start.wait(timeout=5)
        return env.client.post("/exports/jobs", json={"format": "csv", "idempotency_key": str(uuid.uuid4())}, headers=env.headers).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _value: accept(), range(2)))
    assert sorted(results) == [202, 429]
    with Session(env.engine) as db:
        assert db.scalar(select(func.sum(ExportJob.reserved_bytes))) <= settings.export_job_max_reserved_bytes


def test_browser_session_job_survives_navigation_but_not_session_revocation(export_env):
    env = export_env
    with Session(env.engine) as db:
        created = create_auth_session(db, user_id=env.owner_id, auth_method="local", mfa_method=None, client_ip=None, user_agent=None)
        token, session_id = created.token, created.session.id
        db.commit()
    settings = get_settings()
    env.client.cookies.set(settings.auth_cookie_name, token)
    env.client.cookies.set(settings.auth_csrf_cookie_name, "export-csrf")
    payload = {"format": "csv", "filters": {"feed_ids": [str(env.feed_id)]}, "idempotency_key": str(uuid.uuid4())}
    accepted = env.client.post("/exports/jobs", json=payload, headers={settings.auth_csrf_header_name: "export-csrf"})
    assert accepted.status_code == 202, accepted.text
    job_id = uuid.UUID(accepted.json()["id"])
    assert execute_export_job(job_id)["status"] == "ready"
    # A separate request after navigation still sees the durable result.
    assert env.client.get(f"/exports/jobs/{job_id}").json()["download_available"]
    with Session(env.engine) as db:
        db.get(AuthSession, session_id).revoked_at = datetime.now(timezone.utc)
        db.commit()
    env.client.cookies.clear()
    denied = env.client.get(f"/exports/jobs/{job_id}/download", headers=env.headers)
    assert denied.status_code == 409


def test_service_account_job_preserves_machine_options_and_credential_scope(export_env):
    env = export_env
    with Session(env.engine) as db:
        account = ServiceAccount(key=f"export-{uuid.uuid4().hex}", name="Export machine")
        role = IAMRole(key=f"export-{uuid.uuid4().hex}", name="Export machine role", description="", is_system=False)
        db.add_all([account, role])
        db.flush()
        db.add(IAMRolePermission(role_id=role.id, permission="read:items"))
        db.add(ServiceAccountRoleAssignment(service_account_id=account.id, role_id=role.id))
        raw, prefix, digest = _generate_service_account_token()
        credential = ServiceAccountCredential(service_account_id=account.id, name="Export machine", token_prefix=prefix, token_hash=digest,
                                              scopes=["read:items"], expires_at=datetime.now(timezone.utc) + timedelta(days=1))
        db.add(credential)
        db.commit()
        account_id, role_id, credential_id = account.id, role.id, credential.id
    headers = {"Authorization": f"Bearer {raw}"}
    try:
        payload = {"format": "jsonl", "filters": {"feed_ids": [str(env.feed_id)]}, "idempotency_key": str(uuid.uuid4())}
        rejected = env.client.post("/exports/jobs", json={**payload, "options": {"include_user_state": True}}, headers=headers)
        assert rejected.status_code == 400, rejected.text
        accepted = env.client.post("/exports/jobs", json=payload, headers=headers)
        assert accepted.status_code == 202, accepted.text
        job_id = uuid.UUID(accepted.json()["id"])
        assert execute_export_job(job_id)["status"] == "ready"
        assert env.client.get(f"/exports/jobs/{job_id}/download", headers=headers).status_code == 200
        # A human with article access cannot fetch another principal's artifact.
        assert env.client.get(f"/exports/jobs/{job_id}/download", headers=env.headers).status_code == 404
        with Session(env.engine) as db:
            db.get(ServiceAccountCredential, credential_id).scopes = ["read:feeds"]
            db.commit()
        assert env.client.get(f"/exports/jobs/{job_id}/download", headers=headers).status_code == 403
    finally:
        with Session(env.engine) as db:
            db.execute(delete(ExportJob).where(ExportJob.principal_id == account_id))
            db.execute(delete(ServiceAccount).where(ServiceAccount.id == account_id))
            db.execute(delete(IAMRole).where(IAMRole.id == role_id))
            db.commit()


def test_cancelled_claim_scratch_is_cleaned_and_lease_cannot_be_resurrected(export_env):
    env = export_env
    job_id, _ = _accept(env)
    token = claim_export_job(job_id)
    scratch = export_scratch_directory(job_id, token)
    (scratch / "partial.csv").write_bytes(b"private scratch")
    clean_local_export_scratch()
    assert scratch.exists()
    assert env.client.post(f"/exports/jobs/{job_id}/cancel", headers=env.headers).status_code == 200
    with Session(env.engine) as db:
        with pytest.raises(ExportJobInterrupted):
            _owned_job(db, job_id, token)
    clean_local_export_scratch()
    assert not scratch.exists()


def test_celery_soft_deadline_has_a_terminal_timeout_reason(export_env, monkeypatch):
    from billiard.exceptions import SoftTimeLimitExceeded
    from app.services import export_job_worker

    env = export_env
    job_id, _ = _accept(env)
    monkeypatch.setattr(export_job_worker, "_generate", lambda *_args: (_ for _ in ()).throw(SoftTimeLimitExceeded()))
    assert execute_export_job(job_id)["status"] == "failed"
    job = _job(env, job_id)
    assert job.status == "failed"
    assert job.error_code == "generation_timeout"
    assert 0 < job.reserved_bytes < 100_000


def test_accepting_token_scope_reduction_prevents_execution(export_env):
    env = export_env
    job_id, _ = _accept(env)
    with Session(env.engine) as db:
        db.get(ApiToken, env.credential_id).scopes = ["read:feeds"]
        db.commit()
    assert execute_export_job(job_id)["status"] == "failed"
    assert _job(env, job_id).error_code == "authorization_changed"


def test_empty_and_oversized_jobs_fail_without_retaining_an_artifact(export_env, monkeypatch):
    env = export_env
    empty, _ = _accept(env, filters={"feed_ids": [str(uuid.uuid4())]})
    assert execute_export_job(empty)["status"] == "failed"
    assert _job(env, empty).error_code == "empty_export"
    monkeypatch.setattr(get_settings(), "export_max_uncompressed_bytes", 128)
    oversized, _ = _accept(env)
    assert execute_export_job(oversized)["status"] == "failed"
    assert _job(env, oversized).error_code == "size_limit"
    assert _job(env, oversized).file_size is None


def test_coordination_failure_retries_after_backoff_and_then_completes(export_env, monkeypatch):
    from contextlib import contextmanager
    from app.services import export_job_worker
    from app.services.export_lock import ExportLockUnavailableError

    env = export_env
    job_id, _ = _accept(env)
    original = export_job_worker.acquire_export_lock

    @contextmanager
    def unavailable(**_kwargs):
        raise ExportLockUnavailableError("coordination down")
        yield  # pragma: no cover

    monkeypatch.setattr(export_job_worker, "acquire_export_lock", unavailable)
    assert execute_export_job(job_id)["status"] == "failed"
    job = _job(env, job_id)
    assert job.status == "queued"
    assert job.error_code == "coordination_unavailable"
    assert job.attempts == 1
    assert execute_export_job(job_id)["status"] == "skipped"
    with Session(env.engine) as db:
        db.get(ExportJob, job_id).next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    monkeypatch.setattr(export_job_worker, "acquire_export_lock", original)
    assert execute_export_job(job_id)["status"] == "ready"
    assert _job(env, job_id).attempts == 2


def test_worker_renews_lease_during_a_long_render(export_env, monkeypatch):
    from app.services import export_job_worker

    env = export_env
    monkeypatch.setattr(get_settings(), "export_job_lease_seconds", 1)
    job_id, _ = _accept(env)
    original = export_job_worker._generate
    entered = threading.Event()
    release = threading.Event()

    def slow(*args):
        entered.set()
        assert release.wait(timeout=5)
        return original(*args)

    monkeypatch.setattr(export_job_worker, "_generate", slow)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(execute_export_job, job_id)
        try:
            assert entered.wait(timeout=5)
            first = _job(env, job_id).lease_expires_at
            until = time.monotonic() + 3
            while time.monotonic() < until and _job(env, job_id).lease_expires_at <= first:
                time.sleep(0.05)
            assert _job(env, job_id).lease_expires_at > first
            assert claim_export_job(job_id) is None
        finally:
            release.set()
        assert future.result(timeout=5)["status"] == "ready"
