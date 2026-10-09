"""AI worker replies agree with durable qualification and assessment outcomes."""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
import uuid

import pytest

from app.services import ai_qualification
from app.models.ai_task_run import AITaskRun
from app.services.ai_egress_data_policy import AIEgressPolicyError
from app.services.ai_provider_client import AIIntegrationError
from app.tasks import ai_qualification_tasks, team_assessment_tasks


@dataclass(frozen=True)
class _Settings:
    ai_enabled: bool = True
    ai_configured: bool = True
    request_max_retries: int = 0
    request_timeout_seconds: int = 60


@pytest.fixture
def completed_probes(monkeypatch):
    """Resume checkpointed probes without a database, broker, or provider call."""
    run_id = uuid.uuid4()
    row = SimpleNamespace(
        features_json=["report"],
        results_json=[
            {"feature": feature, "state": "completed", "contract_passed": True}
            for feature in ("report", "report_section")
        ],
        reserved_tokens=2400,
        token_budget=24000,
    )
    run = SimpleNamespace(
        status="running",
        celery_task_id="qualification-result-test",
        metadata_json={
            "qualification_plan_sha256": ai_qualification.qualification_plan_fingerprint(
                row.features_json
            )
        },
    )

    class Session:
        def get(self, _model, identity):
            assert identity == run_id
            return run

        def commit(self):
            pass

    db = Session()

    @contextmanager
    def session():
        yield db

    def finish(_db, **kwargs):
        assert kwargs["run_id"] == run_id
        run.status = kwargs["status"]
        run.reason = kwargs["reason"]
        run.error = kwargs["error"]
        run.metadata_json.update(kwargs["metadata_updates"])

    def unexpected_request(*_args, **_kwargs):
        pytest.fail("Checkpointed probes must not send another provider call")

    monkeypatch.setattr(ai_qualification, "qualification_fence", lambda *_args: row)
    monkeypatch.setattr(ai_qualification, "load_active_ai_settings", lambda *_args, **_kwargs: _Settings())
    monkeypatch.setattr(ai_qualification, "finish_ai_task_run", finish)
    monkeypatch.setattr(ai_qualification, "request_ai_json_with_usage", unexpected_request)
    monkeypatch.setattr(ai_qualification_tasks, "SessionLocal", session)
    monkeypatch.setattr(ai_qualification_tasks.ai_ops, "start_ai_task_run", lambda *_args, **_kwargs: run)
    monkeypatch.setattr(ai_qualification_tasks.ai_ops, "ai_task_run_stop_reason", lambda *_args: None)
    return db, run_id, row, run


@pytest.mark.parametrize("passed", [True, False])
def test_service_returns_persisted_contract_outcome(completed_probes, passed):
    db, run_id, row, run = completed_probes
    row.results_json[1]["contract_passed"] = passed
    outcome = ai_qualification.generate_qualification(db, run_id=run_id)

    assert outcome == run.status == ("ready" if passed else "error")
    assert run.reason == ("qualification_contracts_passed" if passed else "qualification_contracts_failed")
    assert run.metadata_json["qualification_contract_passed"] is passed
    assert run.metadata_json["semantic_quality_approved"] is False
    assert bool(run.error) is not passed
    assert row.reserved_tokens == 2400


@pytest.mark.parametrize("passed", [True, False])
def test_worker_result_matches_persisted_contract_outcome(completed_probes, passed):
    _db, run_id, row, run = completed_probes
    row.results_json[1]["contract_passed"] = passed
    worker = ai_qualification_tasks.generate_ai_qualification
    worker.push_request(id=run.celery_task_id, hostname="qualification-result-test")
    try:
        result = worker.run(str(run_id))
    finally:
        worker.pop_request()

    assert result == {"status": "ready" if passed else "error"}
    assert result["status"] == run.status
    assert run.metadata_json["semantic_quality_approved"] is False


@pytest.fixture(params=["qualification", "team_assessment"])
def running_worker(monkeypatch, request):
    """Use real task finalization with mocked persistence and no provider I/O."""
    worker_module = (
        ai_qualification_tasks if request.param == "qualification" else team_assessment_tasks
    )
    failure_reason = (
        "qualification_failed" if request.param == "qualification" else "assessment_generation_failed"
    )
    run = AITaskRun(
        id=uuid.uuid4(), task_type=("connection_test" if request.param == "qualification" else "team_assessment"), trigger_source="manual",
        status="running", celery_task_id="qualification-error-test",
        parent_run_id=None, started_at=datetime.now(timezone.utc), metadata_json={},
    )

    class Session:
        def __init__(self):
            self.selections = 0
            self.committed = []
            self.run_exists = True

        def scalar(self, _statement):
            self.selections += 1
            assert self.selections <= 2
            if not self.run_exists:
                return None
            return run.task_type if self.selections == 1 else run

        def rollback(self):
            pass

        def commit(self):
            self.committed.append((run.status, run.reason) if self.run_exists else None)

        def add(self, _row):
            pass

        def flush(self):
            pass

    db = Session()

    @contextmanager
    def session():
        yield db

    monkeypatch.setattr(worker_module, "SessionLocal", session)
    monkeypatch.setattr(worker_module.ai_ops, "start_ai_task_run", lambda *_args, **_kwargs: run)
    monkeypatch.setattr(worker_module.ai_ops, "settle_pending_ai_resource", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(worker_module.ai_ops, "complete_ai_task_run_data_access", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(worker_module.ai_ops, "record_ai_task_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.services.data_access_runtime.lock_data_policy_revision_for_derivation", lambda *_args: None)
    monkeypatch.setattr("app.services.ai_workflow_dispatch.complete_workflow_dispatch", lambda *_args: None)
    return db, run, worker_module, failure_reason


def invoke_running_worker(run, worker_module):
    worker = (
        ai_qualification_tasks.generate_ai_qualification
        if worker_module is ai_qualification_tasks else team_assessment_tasks.generate_team_assessment
    )
    worker.push_request(id="qualification-error-test", hostname="qualification-error-test")
    try:
        return worker.run(str(run.id))
    finally:
        worker.pop_request()


@pytest.mark.parametrize("failure_kind", ["integration", "unexpected"])
def test_worker_error_respects_committed_cancellation(running_worker, monkeypatch, failure_kind):
    db, run, worker_module, _failure_reason = running_worker

    def canceled_probe(*_args, **_kwargs):
        run.metadata_json = {"cancel_requested_at": datetime.now(timezone.utc).isoformat()}
        run.reason = "cancel_requested"
        if failure_kind == "integration":
            raise AIEgressPolicyError("Provider qualification was canceled or replaced.", retryable=False)
        raise RuntimeError("Synthetic probe failure after cancellation")

    monkeypatch.setattr(worker_module, "generate_assessment", canceled_probe)
    result = invoke_running_worker(run, worker_module)

    assert db.committed[-1] == ("skipped", "canceled")
    assert result == {"status": "skipped", "reason": "canceled"}
    assert run.error is None
    assert run.finished_at is not None


@pytest.mark.parametrize("failure_kind", ["integration", "unexpected"])
def test_worker_genuine_error_matches_committed_failure(running_worker, monkeypatch, failure_kind):
    db, run, worker_module, failure_reason = running_worker

    def failed_probe(*_args, **_kwargs):
        if failure_kind == "integration":
            raise AIIntegrationError("Synthetic provider failure", retryable=False)
        raise RuntimeError("Synthetic worker failure")

    monkeypatch.setattr(worker_module, "generate_assessment", failed_probe)
    result = invoke_running_worker(run, worker_module)
    reason = failure_reason if failure_kind == "integration" else "unexpected_error"

    assert db.committed[-1] == ("error", reason)
    assert result == {"status": "error", "reason": reason}
    assert run.error
    assert run.finished_at is not None


@pytest.mark.parametrize("failure_kind", ["integration", "unexpected"])
def test_worker_error_cannot_claim_a_superseded_delivery(running_worker, monkeypatch, failure_kind):
    db, run, worker_module, _failure_reason = running_worker

    def replaced_probe(*_args, **_kwargs):
        run.celery_task_id = "replacement-delivery"
        if failure_kind == "integration":
            raise AIIntegrationError("Synthetic old-delivery failure", retryable=False)
        raise RuntimeError("Synthetic old-worker failure")

    monkeypatch.setattr(worker_module, "generate_assessment", replaced_probe)
    result = invoke_running_worker(run, worker_module)

    assert db.committed[-1] == ("running", None)
    assert result == {"status": "skipped", "reason": "superseded_delivery"}
    assert run.celery_task_id == "replacement-delivery"
    assert run.error is None
    assert run.finished_at is None


@pytest.mark.parametrize("failure_kind", ["integration", "unexpected"])
def test_worker_error_handles_a_run_removed_during_the_probe(running_worker, monkeypatch, failure_kind):
    db, run, worker_module, _failure_reason = running_worker

    def removed_probe(*_args, **_kwargs):
        db.run_exists = False
        if failure_kind == "integration":
            raise AIIntegrationError("Synthetic provider failure after removal", retryable=False)
        raise RuntimeError("Synthetic worker failure after removal")

    monkeypatch.setattr(worker_module, "generate_assessment", removed_probe)
    result = invoke_running_worker(run, worker_module)

    assert db.selections == 2
    assert db.committed[-1] is None
    assert result == {"status": "skipped", "reason": "task_not_found"}
    assert run.error is None
    assert run.finished_at is None


@pytest.mark.parametrize("failure_kind", ["integration", "unexpected"])
@pytest.mark.parametrize("terminal_reason", [None, "completion_recovered"])
def test_worker_error_preserves_an_already_committed_success(running_worker, monkeypatch, failure_kind, terminal_reason):
    db, run, worker_module, _failure_reason = running_worker

    def settled_probe(*_args, **_kwargs):
        run.status = "ready"
        run.reason = terminal_reason
        run.finished_at = datetime.now(timezone.utc)
        if failure_kind == "integration":
            raise AIIntegrationError("Synthetic late provider failure", retryable=False)
        raise RuntimeError("Synthetic late worker failure")

    monkeypatch.setattr(worker_module, "generate_assessment", settled_probe)
    result = invoke_running_worker(run, worker_module)

    assert db.committed[-1] == ("ready", terminal_reason)
    assert result == ({"status": "ready"} if terminal_reason is None else {"status": "ready", "reason": terminal_reason})
    assert run.error is None
