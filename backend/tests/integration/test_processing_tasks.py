"""Task dispatch owns committed claims even when broker acknowledgement is lost."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.item import Item
from app.models.item_classification import ItemClassification
from app.models.processing_work import ProcessingRecoveryRun, ProcessingWork
from app.tasks import processing_tasks as tasks
from tests.integration.test_export_jobs import export_env as export_env
from tests.integration.test_processing_recovery import (
    _accept,
    processing_env as processing_env,
)


@pytest.mark.parametrize(("work_id", "token"), [
    ("not-a-uuid", str(uuid.UUID(int=1))),
    (str(uuid.UUID(int=1)), "not-a-uuid"),
    (None, None), ([], {}), (1, 1),
])
def test_processing_task_rejects_invalid_identity_before_execution(monkeypatch, work_id, token):
    def unexpected(*_args, **_kwargs):
        pytest.fail("Invalid identity reached processing execution")

    monkeypatch.setattr(tasks, "execute_processing_work", unexpected)
    assert tasks.process_work(work_id, token) == {
        "status": "skipped", "reason": "invalid_identity",
    }


@pytest.mark.parametrize("stage", ["", "unknown", "article;delete", [], {}, 5])
def test_dispatch_rejects_invalid_stage_before_telemetry_or_database(monkeypatch, stage):
    def unexpected(*_args, **_kwargs):
        pytest.fail("Invalid stage reached external work")

    monkeypatch.setattr(tasks, "read_queue_execution_canaries", unexpected)
    monkeypatch.setattr(tasks, "db_session", unexpected)
    assert tasks.dispatch_processing_work(stage) == {
        "status": "skipped", "reason": "invalid_stage",
    }


@pytest.mark.parametrize("initial_canary", [None, datetime(2026, 1, 1, tzinfo=timezone.utc)])
def test_ambiguous_dispatch_keeps_claim_until_later_consumer_canary(
    processing_env, monkeypatch, initial_canary,
):
    env = processing_env
    _accept(env)
    canary = SimpleNamespace(value=initial_canary)
    observed = []

    def read_canaries(**kwargs):
        assert kwargs["queues"] == ["processing"]
        return {} if canary.value is None else {
            "processing": SimpleNamespace(heartbeat_at=canary.value),
        }

    def accepted_but_disconnected(*, args):
        identity, token = map(uuid.UUID, args)
        with Session(env.engine) as db:
            work = db.get(ProcessingWork, identity)
            # Broker I/O begins only after the durable claim is visible elsewhere.
            assert work.status == "queued" and work.claim_token == token
            assert work.published_at is not None and work.lease_expires_at > work.published_at
        observed.append((identity, token))
        raise ConnectionError("Synthetic acknowledgement lost")

    monkeypatch.setattr(tasks, "read_queue_execution_canaries", read_canaries)
    monkeypatch.setattr(tasks.process_work, "apply_async", accepted_but_disconnected)
    result = tasks.dispatch_processing_work("classification")
    assert result["published"] == 0 and len(observed) == 1
    identity, first_token = observed[0]
    with Session(env.engine) as db:
        work = db.get(ProcessingWork, identity)
        work.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        original_canary = work.published_canary_at
        db.commit()

    # Neither no heartbeat nor the same heartbeat proves the consumer advanced.
    for heartbeat in (None, original_canary):
        canary.value = heartbeat
        assert tasks.dispatch_processing_work("classification")["published"] == 0
        assert observed == [(identity, first_token)]
        with Session(env.engine) as db:
            assert db.get(ProcessingWork, identity).claim_token == first_token

    canary.value = original_canary + timedelta(seconds=1)
    published = []
    monkeypatch.setattr(tasks.process_work, "apply_async", lambda **kwargs: published.append(kwargs["args"]))
    assert tasks.dispatch_processing_work("classification")["published"] == 1
    assert len(published) == 1
    assert published[0][0] == str(identity) and published[0][1] != str(first_token)
    assert tasks.process_work(str(identity), str(first_token))["reason"] == "stale_claim"
    assert tasks.process_work(*published[0])["status"] == "succeeded"
    with Session(env.engine) as db:
        item = db.get(Item, env.item_id)
        assert item.classification_completed_version == item.classification_required_version
        assert db.get(ItemClassification, env.item_id) is not None


def test_dispatch_commits_repair_and_pruning_before_later_discovery_failure(processing_env, monkeypatch):
    env = processing_env
    _accept(env)
    with Session(env.engine) as db:
        work = db.scalar(select(ProcessingWork).where(ProcessingWork.item_id == env.item_id))
        work.status, work.attempts = "running", 1
        work.claim_token = uuid.uuid4()
        work.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        old_run = ProcessingRecoveryRun(
            principal_type="user", principal_id=env.owner_id,
            idempotency_key=uuid.uuid4(), request_hash="a" * 64,
            authorization_encrypted={}, source_encrypted={}, total_count=1,
            status="succeeded", updated_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
        )
        db.add(old_run)
        db.flush()
        work_id, old_run_id = work.id, old_run.id
        db.commit()

    def discovery_failed(*_args, **_kwargs):
        raise RuntimeError("Synthetic discovery failure")

    monkeypatch.setattr(tasks, "read_queue_execution_canaries", lambda **_kwargs: {})
    monkeypatch.setattr(tasks, "discover_processing_work", discovery_failed)
    with pytest.raises(RuntimeError, match="Synthetic discovery failure"):
        tasks.dispatch_processing_work("classification")
    with Session(env.engine) as db:
        work = db.get(ProcessingWork, work_id)
        assert work.status == "retry_wait" and work.reason == "worker_interrupted"
        assert work.claim_token is None and work.lease_expires_at is None
        assert work.next_retry_at > datetime.now(timezone.utc)
        assert db.get(ProcessingRecoveryRun, old_run_id) is None
