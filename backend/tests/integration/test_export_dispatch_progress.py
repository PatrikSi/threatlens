"""Publication ambiguity must not turn paused export consumers into queue growth."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy.orm import Session

from app.models.export_job import ExportJob
from app.services.export_job_dispatch import reserve_export_publications
from app.services.export_jobs import maintain_export_jobs
from app.services import export_job_worker as worker
from app.tasks import export_tasks
from tests.integration.test_export_jobs import _accept, _job, export_env as export_env


def _elapsed_grace(env, job_id):
    with Session(env.engine) as db:
        job = db.get(ExportJob, job_id)
        job.published_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        job.next_dispatch_at = job.published_at
        db.commit()


@pytest.mark.parametrize("uncertain", [False, True])
def test_paused_consumers_do_not_accumulate_duplicate_export_messages(export_env, monkeypatch, uncertain):
    env = export_env
    published = []

    def publish(**kwargs):
        published.append(kwargs)
        job = _job(env, uuid.UUID(kwargs["args"][0]))
        assert job.published_at is not None  # Durable before broker I/O.
        if uncertain:
            raise ConnectionError("Broker accepted but acknowledgement was lost")

    monkeypatch.setattr(export_tasks.generate_export_job, "apply_async", publish)
    job_id, _ = _accept(env)
    assert len(published) == 1
    first = _job(env, job_id)
    _elapsed_grace(env, job_id)
    for reason in ("missing", "redis_unavailable", "stale", "future", "fresh"):
        # Even a still-fresh unchanged canary cannot authorize another publish.
        monkeypatch.setattr(export_tasks, "read_queue_execution_canaries", lambda reason=reason, **_kwargs: {
            "exports-v1": SimpleNamespace(reason=reason, heartbeat_at=first.published_canary_at),
        })
        assert export_tasks.dispatch_export_jobs()["queued"] == 0
        assert export_tasks.enqueue_export_job(job_id) is False
    assert len(published) == 1
    assert _job(env, job_id).status == "queued"
    assert _job(env, job_id).attempts == 0


def test_lost_broker_message_recovers_after_consumer_resumes(export_env, monkeypatch):
    env = export_env
    job_id, _ = _accept(env)
    _elapsed_grace(env, job_id)
    observed = []
    monkeypatch.setattr(export_tasks.generate_export_job, "apply_async", lambda **kwargs: observed.append(kwargs))
    resumed_at = datetime.now(timezone.utc)
    monkeypatch.setattr(export_tasks, "read_queue_execution_canaries", lambda **_kwargs: {
        "exports-v1": SimpleNamespace(reason="fresh", heartbeat_at=resumed_at),
    })
    assert export_tasks.dispatch_export_jobs()["queued"] == 1
    assert observed[0]["args"] == [str(job_id)]
    assert export_tasks.dispatch_export_jobs()["queued"] == 0
    assert worker.execute_export_job(job_id)["status"] == "ready"
    assert worker.execute_export_job(job_id)["status"] == "skipped"


def test_expired_running_claim_can_retry_without_fabricated_consumer_progress(export_env):
    env = export_env
    job_id, _ = _accept(env)
    assert worker.claim_export_job(job_id) is not None
    with Session(env.engine) as db:
        job = db.get(ExportJob, job_id)
        job.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
        assert maintain_export_jobs(db) == 1
        db.commit()
    assert _job(env, job_id).published_at is None
    assert export_tasks.dispatch_export_jobs()["queued"] == 1
    assert worker.execute_export_job(job_id)["status"] == "ready"


def test_concurrent_publishers_reserve_only_one_wakeup(export_env):
    env = export_env
    job_id, _ = _accept(env)
    _elapsed_grace(env, job_id)
    canary_at = datetime.now(timezone.utc)
    with Session(env.engine) as first, Session(env.engine) as second:
        assert reserve_export_publications(first, job_id=job_id, canary_at=canary_at) == [job_id]
        assert reserve_export_publications(second, job_id=job_id, canary_at=canary_at) == []
        second.rollback()
        first.commit()
    assert export_tasks.dispatch_export_jobs()["queued"] == 0


def test_recovery_preserves_recorded_retry_delay(export_env):
    env = export_env
    job_id, _ = _accept(env)
    with Session(env.engine) as db:
        job = db.get(ExportJob, job_id)
        job.published_at = None
        job.published_canary_at = None
        job.next_attempt_at = datetime.now(timezone.utc) + timedelta(minutes=5)
        job.next_dispatch_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
    assert export_tasks.dispatch_export_jobs()["queued"] == 0
