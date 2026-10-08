"""Worker completion must agree with the durable contract qualification outcome."""

from contextlib import contextmanager
from dataclasses import dataclass
from types import SimpleNamespace
import uuid

import pytest

from app.services import ai_qualification
from app.tasks import ai_qualification_tasks


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
