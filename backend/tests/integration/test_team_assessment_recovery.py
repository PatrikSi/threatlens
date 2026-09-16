"""Exercise scheduler selection and real worker fencing after interrupted work."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select

from app.models.ai_provider_attempt_receipt import AIProviderAttemptReceipt
from app.models.ai_workflow import AIWorkflowDispatch
from app.services import ai_ops
from app.services.ai_workflow_recovery import adopt_legacy_workflows
from tests.integration.test_team_assessment_worker import (
    _completion,
    accepted_assessment as accepted_assessment,
    invoke_worker,
)


def _lost_worker(db, run):
    ai_ops.start_ai_task_run(db, run_id=run.id, celery_task_id="lost-delivery")
    run.updated_at = datetime.now(timezone.utc) - timedelta(hours=2)
    db.commit()


def _reconcile(db, *, snapshot_available):
    ai_ops._reconcile_stale_ai_runs(
        db, snapshot_available=snapshot_available,
        workers=[], active_tasks=[], reserved_tasks=[], scheduled_tasks=[],
    )
    db.expire_all()


@pytest.mark.parametrize("snapshot_available", [True, False])
def test_scheduler_recovers_unsent_team_work_and_fences_old_delivery(
    db_session, accepted_assessment, monkeypatch, snapshot_available,
):
    run, row = accepted_assessment["run"], accepted_assessment["row"]
    _lost_worker(db_session, run)
    _reconcile(db_session, snapshot_available=snapshot_available)
    assert run.status == "queued"
    assert run.celery_task_id != "lost-delivery"
    assert run.finished_at is None
    dispatch = db_session.get(AIWorkflowDispatch, run.id)
    assert dispatch.state == "pending"
    assert dispatch.delivery_id == run.celery_task_id
    recovered_delivery = run.celery_task_id
    calls = []

    def provider(*_args, **_kwargs):
        calls.append(True)
        return _completion()

    monkeypatch.setattr("app.services.ai_integration._call_ai_json", provider)
    assert invoke_worker(db_session, monkeypatch, run.id, delivery="lost-delivery")["status"] == "skipped"
    assert calls == []
    assert row.result_json is None
    assert invoke_worker(db_session, monkeypatch, run.id, delivery=recovered_delivery)["status"] == "ready"
    assert calls == [True]
    assert row.result_json is not None


@pytest.mark.parametrize("receipt_state", ["reserved", "ambiguous", "succeeded"])
def test_scheduler_settles_interrupted_team_io_without_replaying_provider(
    db_session, accepted_assessment, monkeypatch, receipt_state,
):
    run = accepted_assessment["run"]
    _lost_worker(db_session, run)
    receipt = AIProviderAttemptReceipt(
        operation_id=uuid.uuid4(), attempt_number=1, request_fingerprint="a" * 64,
        task_run_id_snapshot=run.id, feature_type="team_assessment",
        resource_type="item", resource_id=run.item_id, max_attempts=3,
        requested_max_tokens=1000, iam_revision=1, data_policy_revision=1,
        data_policy_mode="disabled", state=receipt_state,
        io_outcome="response_received" if receipt_state == "succeeded" else receipt_state,
        retryable=None if receipt_state == "reserved" else False,
        settled_at=None if receipt_state == "reserved" else datetime.now(timezone.utc),
    )
    db_session.add(receipt)
    db_session.commit()
    _reconcile(db_session, snapshot_available=True)
    assert run.status == "error"
    assert run.reason == "stale_task_lost"
    assert run.finished_at is not None
    assert db_session.get(AIWorkflowDispatch, run.id).state == "complete"
    assert db_session.scalar(select(AIProviderAttemptReceipt.state)) == receipt_state

    def forbidden(*_args, **_kwargs):
        pytest.fail("Interrupted or completed provider I/O must not be repeated")

    monkeypatch.setattr("app.services.ai_integration._call_ai_json", forbidden)
    assert invoke_worker(db_session, monkeypatch, run.id, delivery="lost-delivery")["status"] == "skipped"
    assert accepted_assessment["row"].result_json is None


def test_legacy_adoption_selects_team_work_missing_its_dispatch(db_session, accepted_assessment):
    run = accepted_assessment["run"]
    db_session.execute(delete(AIWorkflowDispatch).where(AIWorkflowDispatch.run_id == run.id))
    db_session.commit()
    assert adopt_legacy_workflows(db_session, limit=20) == 1
    db_session.commit()
    dispatch = db_session.get(AIWorkflowDispatch, run.id)
    assert dispatch.task_name == "app.tasks.team_assessment_tasks.generate_team_assessment"
    assert dispatch.payload_json["task_run_id"] == str(run.id)
    assert dispatch.state == "pending"
