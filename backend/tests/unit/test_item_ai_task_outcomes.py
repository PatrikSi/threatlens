"""Item worker replies retain finalization and delivery ownership outcomes."""

from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
import uuid

import pytest

from app.models.ai_task_run import AITaskRun
from app.core.logging_config import ThreatLensJsonFormatter, ThreatLensTextFormatter
from app.services.ai_execution_ownership import AIExecutionSuperseded
from app.services.ai_provider_client import AIIntegrationError
from app.tasks import item_ai_tasks
from app.tasks.feed_task_dependencies import ItemAIDependencies


@pytest.fixture()
def worker(monkeypatch):
    run = AITaskRun(
        id=uuid.uuid4(), item_id=uuid.uuid4(), task_type="item_enrichment",
        trigger_source="manual", status="running", celery_task_id="item-outcome-test",
        parent_run_id=None, started_at=datetime.now(timezone.utc), metadata_json={},
    )

    class Session:
        run_exists = True
        selections = 0

        def scalar(self, _statement):
            self.selections += 1
            assert self.selections <= 2
            if not self.run_exists:
                return None
            return run.task_type if self.selections == 1 else run

        def commit(self):
            pass

        def rollback(self):
            pass

        def add(self, _row):
            pass

        def flush(self):
            pass

    db = Session()

    @contextmanager
    def session():
        yield db

    monkeypatch.setattr(item_ai_tasks, "_start_item_run", lambda *_args: None)
    monkeypatch.setattr(item_ai_tasks.feed_task_runtime, "claim_item_processing_target", lambda *_args, **_kwargs: (None, None))
    monkeypatch.setattr(item_ai_tasks.ai_ops, "settle_pending_ai_resource", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(item_ai_tasks.ai_ops, "complete_ai_task_run_data_access", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(item_ai_tasks.ai_ops, "record_ai_task_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.services.data_access_runtime.lock_data_policy_revision_for_derivation", lambda *_args: None)
    monkeypatch.setattr("app.services.ai_workflow_dispatch.complete_workflow_dispatch", lambda *_args: None)
    task = SimpleNamespace(request=SimpleNamespace(id=run.celery_task_id, hostname="test"))
    dependencies = ItemAIDependencies(
        db_session=session, queue_ai_enrichment=lambda **_kwargs: pytest.fail("Unexpected queue publication"),
        ai_run_stop_reason=lambda _run_id: None,
    )
    return db, run, task, dependencies


def invoke(worker):
    _db, run, task, dependencies = worker
    return item_ai_tasks.run_generate_item_ai_enrichment(
        task, str(run.item_id), task_run_id=str(run.id), dependencies=dependencies,
    )


@pytest.mark.parametrize("generation", ["result", "unexpected"])
@pytest.mark.parametrize("state", ["canceled", "superseded_delivery", "task_not_found"])
def test_item_worker_result_matches_finalization(worker, monkeypatch, generation, state):
    db, run, _task, _dependencies = worker

    def generate(*_args, **_kwargs):
        if state == "canceled":
            run.metadata_json = {"cancel_requested_at": datetime.now(timezone.utc).isoformat()}
        elif state == "superseded_delivery":
            run.celery_task_id = "replacement-item-delivery"
        else:
            db.run_exists = False
        if generation == "unexpected":
            raise RuntimeError("Synthetic item failure")
        return SimpleNamespace(
            status="skipped", reason="not_eligible", enrichment=None, error=None,
            prompt_char_count=0, response_char_count=0, input_text_chars=0,
        )

    monkeypatch.setattr(item_ai_tasks.ai_integration, "run_item_ai_enrichment", generate)
    assert invoke(worker) == {"status": "skipped", "reason": state, "item_id": str(run.item_id)}
    if state == "canceled":
        assert run.status == "skipped" and run.reason == "canceled"
        assert run.error is None and run.finished_at is not None
    else:
        assert run.status == "running" and run.error is None and run.finished_at is None


def test_item_worker_ownership_exception_remains_a_skip(worker, monkeypatch):
    _db, run, _task, _dependencies = worker
    def superseded(*_args, **_kwargs):
        run.celery_task_id = "replacement-item-delivery"
        raise AIExecutionSuperseded("Synthetic replaced item delivery")

    monkeypatch.setattr(item_ai_tasks.ai_integration, "run_item_ai_enrichment", superseded)
    assert invoke(worker) == {"status": "skipped", "reason": "superseded_delivery"}
    db, run, _task, _dependencies = worker
    assert db.selections == 0
    assert run.status == "running" and run.finished_at is None


def test_item_worker_genuine_failure_keeps_committed_error(worker, monkeypatch):
    def failed(*_args, **_kwargs):
        raise RuntimeError("Synthetic ordinary item failure")

    monkeypatch.setattr(item_ai_tasks.ai_integration, "run_item_ai_enrichment", failed)
    _db, run, _task, _dependencies = worker
    assert invoke(worker) == {"status": "error", "reason": "unexpected_error", "item_id": str(run.item_id)}
    assert run.status == "error" and run.reason == "unexpected_error"
    assert run.error and run.finished_at is not None


@pytest.mark.parametrize("generation", ["result", "unexpected"])
@pytest.mark.parametrize("terminal_reason", [None, "completion_recovered"])
def test_item_worker_preserves_a_previously_committed_success(worker, monkeypatch, generation, terminal_reason):
    _db, run, _task, _dependencies = worker

    def settled(*_args, **_kwargs):
        run.status = "ready"
        run.reason = terminal_reason
        run.finished_at = datetime.now(timezone.utc)
        if generation == "unexpected":
            raise RuntimeError("Synthetic failure after another delivery completed")
        return SimpleNamespace(
            status="skipped", reason="not_eligible", enrichment=None, error=None,
            prompt_char_count=0, response_char_count=0, input_text_chars=0,
        )

    monkeypatch.setattr(item_ai_tasks.ai_integration, "run_item_ai_enrichment", settled)
    assert invoke(worker) == {"status": "ready", "reason": terminal_reason, "item_id": str(run.item_id)}
    assert run.status == "ready" and run.reason == terminal_reason and run.error is None


def test_item_error_logs_exclude_provider_text_from_a_failed_settlement_chain(worker, monkeypatch, caplog):
    sentinel = "SYNTHETIC_CLASSIFIED_ARTICLE_SENTINEL"

    def settlement_failed(*_args, **_kwargs):
        try:
            raise AIIntegrationError(sentinel, retryable=False)
        except AIIntegrationError as exc:
            raise RuntimeError("Synthetic database failure while settling provider error") from exc

    monkeypatch.setattr(item_ai_tasks.ai_integration, "run_item_ai_enrichment", settlement_failed)
    with caplog.at_level("ERROR", logger=item_ai_tasks.__name__):
        result = invoke(worker)
    _db, run, _task, _dependencies = worker
    assert result == {"status": "error", "reason": "unexpected_error", "item_id": str(run.item_id)}
    assert run.status == "error" and run.reason == "unexpected_error"
    records = [record for record in caplog.records if record.name == item_ai_tasks.__name__]
    assert len(records) == 1
    record = records[0]
    assert record.exc_info is None
    assert "error_type=RuntimeError" in record.getMessage()
    assert str(run.id) in record.getMessage()
    assert sentinel not in caplog.text
    for formatter in (ThreatLensTextFormatter(max_chars=20_000), ThreatLensJsonFormatter(max_chars=20_000)):
        assert sentinel not in formatter.format(record)
