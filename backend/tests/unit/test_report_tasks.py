import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from celery.exceptions import Retry

from app.models.ai_task_run import AITaskRun
from app.models.report import Report
from app.models.report_generation_lease import ReportGenerationLease
from app.services.ai_ops import queue_ai_task_run
from app.services.ai_ops_common import AI_TASK_TYPE_REPORT
from app.services.report_generation import ReportGenerationError
from app.tasks import report_tasks


def _report(*, lease_token: str | None = None) -> Report:
    now = datetime.now(timezone.utc)
    return Report(
        id=uuid.uuid4(),
        title="Task test",
        report_type="custom",
        status="queued",
        trigger_source="manual",
        generation_stage="queued",
        period_start=now - timedelta(days=1),
        period_end=now,
        filters_json={},
        prompt_config_json={},
        sections_config_json=[],
        metrics_json={},
        coverage_json={},
        generation_lease_token=lease_token,
        generation_lease_expires_at=(
            now + timedelta(minutes=10) if lease_token else None
        ),
    )


def _task_run(db_session, report: Report) -> AITaskRun:
    db_session.add(report)
    db_session.flush()
    run = queue_ai_task_run(
        db_session,
        task_type=AI_TASK_TYPE_REPORT,
        trigger_source="manual",
        report_id=report.id,
    )
    run.celery_task_id = "report-task"
    db_session.commit()
    return run


def _use_test_session(monkeypatch, db_session) -> None:
    @contextmanager
    def _session():
        try:
            yield db_session
        except Exception:
            db_session.rollback()
            raise

    monkeypatch.setattr(report_tasks, "db_session", _session)


def test_report_task_retries_redelivery_while_another_lease_is_active(
    db_session, monkeypatch
):
    report = _report(lease_token="active-worker")
    run = _task_run(db_session, report)
    _use_test_session(monkeypatch, db_session)
    monkeypatch.setattr(
        report_tasks,
        "generate_report",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("duplicate task must not generate the report")
        ),
    )

    with pytest.raises(Retry):
        report_tasks.generate_intelligence_report.run(
            str(report.id),
            str(run.id),
        )

    db_session.expire_all()
    assert db_session.get(Report, report.id).status == "queued"


def test_report_task_allows_unbounded_ownership_waits():
    assert report_tasks.generate_intelligence_report.max_retries is None


def test_report_infrastructure_retry_uses_classified_exponential_countdown():
    captured = {}

    class RetryTask:
        request = SimpleNamespace(
            headers={
                report_tasks.REPORT_INFRASTRUCTURE_RETRY_HEADER: 2,
                "trace-context": "preserved",
            }
        )

        def retry(self, **kwargs):
            captured.update(kwargs)
            return Retry()

    with pytest.raises(Retry):
        report_tasks._retry_or_settle_report_infrastructure(
            RetryTask(),
            report_id=uuid.uuid4(),
            run_id=uuid.uuid4(),
            worker_name="worker@example",
            lease_token="lease-token",
            generation_fence=None,
            infrastructure_retry_count=2,
            phase="starting report generation",
            exc=ConnectionError("database unavailable"),
        )

    assert captured["countdown"] == 120
    assert captured["max_retries"] is None
    assert captured["kwargs"] == {}
    assert captured["headers"] == {
        report_tasks.REPORT_INFRASTRUCTURE_RETRY_HEADER: 3,
        "trace-context": "preserved",
    }


def test_report_task_terminalizes_exhausted_startup_failures(
    db_session,
    monkeypatch,
):
    report = _report()
    run = _task_run(db_session, report)
    _use_test_session(monkeypatch, db_session)
    monkeypatch.setattr(
        report_tasks,
        "start_ai_task_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("permanent startup fault")
        ),
    )

    result = report_tasks.generate_intelligence_report.apply(
        args=[str(report.id), str(run.id)],
        headers={
            report_tasks.REPORT_INFRASTRUCTURE_RETRY_HEADER: (
                report_tasks.settings.report_task_infrastructure_max_retries
            )
        },
        task_id="report-task",
    ).get()

    db_session.expire_all()
    stored_report = db_session.get(Report, report.id)
    stored_run = db_session.get(AITaskRun, run.id)
    assert result == {"status": "error", "reason": "worker_infrastructure_error"}
    assert stored_report.status == "error"
    assert stored_report.error_code == "worker_infrastructure_error"
    assert "infrastructure retries" in stored_report.error
    assert stored_run.status == "error"
    assert stored_run.reason == "worker_infrastructure_error"
    assert stored_run.metadata_json["infrastructure_failure_phase"] == (
        "starting report generation"
    )


def test_exhausted_report_task_defers_to_foreign_generation_owner(
    db_session,
    monkeypatch,
):
    owner_token = "active-foreign-worker"
    report = _report(lease_token=owner_token)
    run = _task_run(db_session, report)
    db_session.add(
        ReportGenerationLease(
            report_id=report.id,
            generation_fence=7,
            lease_token=owner_token,
            lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
    )
    db_session.commit()
    _use_test_session(monkeypatch, db_session)

    result = report_tasks._settle_exhausted_report_infrastructure(
        report_id=report.id,
        run_id=run.id,
        worker_name="exhausted-worker",
        lease_token="different-worker",
        generation_fence=None,
        retry_count=3,
        phase="starting report generation",
    )

    db_session.expire_all()
    assert result == {
        "status": "error",
        "reason": "worker_infrastructure_reconciliation_pending",
    }
    assert db_session.get(Report, report.id).status == "queued"
    assert db_session.get(AITaskRun, run.id).status == "queued"
    assert db_session.get(ReportGenerationLease, report.id).lease_token == owner_token


def test_exhausted_report_task_recovers_ambiguous_committed_claim(
    db_session,
    monkeypatch,
):
    owner_token = "ambiguous-commit-worker"
    report = _report(lease_token=owner_token)
    run = _task_run(db_session, report)
    db_session.add(
        ReportGenerationLease(
            report_id=report.id,
            generation_fence=9,
            lease_token=owner_token,
            lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
    )
    db_session.commit()
    _use_test_session(monkeypatch, db_session)

    result = report_tasks._settle_exhausted_report_infrastructure(
        report_id=report.id,
        run_id=run.id,
        worker_name="ambiguous-worker",
        lease_token=owner_token,
        generation_fence=None,
        retry_count=3,
        phase="committing generation ownership",
    )

    db_session.expire_all()
    stored_report = db_session.get(Report, report.id)
    stored_lease = db_session.get(ReportGenerationLease, report.id)
    assert result == {"status": "error", "reason": "worker_infrastructure_error"}
    assert stored_report.status == "error"
    assert stored_report.generation_lease_token is None
    assert stored_lease.lease_token is None


def test_report_task_uses_only_committed_claim_for_exhaustion(
    db_session,
    monkeypatch,
):
    report = _report()
    run = _task_run(db_session, report)
    commit_calls = 0

    class FailFirstCommit:
        def __getattr__(self, name):
            return getattr(db_session, name)

        def commit(self):
            nonlocal commit_calls
            commit_calls += 1
            if commit_calls == 1:
                raise RuntimeError("claim commit failed")
            db_session.commit()

    @contextmanager
    def _session():
        try:
            yield FailFirstCommit()
        except Exception:
            db_session.rollback()
            raise

    monkeypatch.setattr(report_tasks, "db_session", _session)

    result = report_tasks.generate_intelligence_report.apply(
        args=[str(report.id), str(run.id)],
        headers={
            report_tasks.REPORT_INFRASTRUCTURE_RETRY_HEADER: (
                report_tasks.settings.report_task_infrastructure_max_retries
            )
        },
        task_id="report-task",
    ).get()

    db_session.expire_all()
    assert result == {"status": "error", "reason": "worker_infrastructure_error"}
    assert db_session.get(Report, report.id).status == "error"
    assert db_session.get(AITaskRun, run.id).status == "error"


def test_report_task_persists_legacy_guard_before_retry(db_session, monkeypatch):
    report = _report()
    report.status = "running"
    report.started_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    run = _task_run(db_session, report)
    _use_test_session(monkeypatch, db_session)

    with pytest.raises(Retry):
        report_tasks.generate_intelligence_report.run(str(report.id), str(run.id))

    db_session.expire_all()
    stored_report = db_session.get(Report, report.id)
    lease = db_session.get(ReportGenerationLease, report.id)
    expected_token = f"legacy-unfenced:{report.id.hex}"
    assert stored_report.generation_lease_token == expected_token
    assert lease is not None and lease.lease_token == expected_token

    expired_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    stored_report.generation_lease_expires_at = expired_at
    lease.lease_expires_at = expired_at
    db_session.commit()

    result = report_tasks.generate_intelligence_report.apply(
        args=[str(report.id), str(run.id)], task_id="report-task"
    ).get()
    db_session.expire_all()
    assert result == {"status": "error", "reason": "generation_interrupted"}
    assert db_session.get(Report, report.id).status == "error"


def test_report_task_does_not_resume_expired_running_generation(
    db_session,
    monkeypatch,
):
    report = _report(lease_token="lost-worker")
    report.status = "running"
    report.generation_stage = "section:findings"
    report.started_at = datetime.now(timezone.utc) - timedelta(minutes=30)
    report.generation_lease_expires_at = datetime.now(timezone.utc) - timedelta(
        seconds=1
    )
    report.model_calls = 3
    run = _task_run(db_session, report)
    _use_test_session(monkeypatch, db_session)
    generation_calls = []
    claim_results = []
    original_claim = report_tasks.claim_report_generation

    def _capture_claim(*args, **kwargs):
        claim = original_claim(*args, **kwargs)
        claim_results.append(claim)
        return claim

    def _unexpected_generation(*_args, **_kwargs):
        generation_calls.append(True)
        raise AssertionError("expired work must not repeat provider calls")

    monkeypatch.setattr(
        report_tasks,
        "generate_report",
        _unexpected_generation,
    )
    monkeypatch.setattr(report_tasks, "claim_report_generation", _capture_claim)

    result = report_tasks.generate_intelligence_report.apply(
        args=[str(report.id), str(run.id)], task_id="report-task"
    ).get()

    db_session.expire_all()
    stored_report = db_session.get(Report, report.id)
    stored_run = db_session.get(AITaskRun, run.id)
    assert generation_calls == []
    assert claim_results[0].status == "interrupted"
    assert result == {"status": "error", "reason": "generation_interrupted"}
    assert stored_report.status == "error"
    assert stored_report.error_code == "generation_interrupted"
    assert stored_report.model_calls == 3
    assert stored_run.status == "error"
    assert stored_run.metadata_json["automatic_resume_skipped"] is True


def test_report_task_records_cancellation_as_skipped(db_session, monkeypatch):
    report = _report()
    run = _task_run(db_session, report)
    _use_test_session(monkeypatch, db_session)
    monkeypatch.setattr(
        report_tasks,
        "generate_report",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ReportGenerationError("Report generation was canceled.", code="canceled")
        ),
    )

    result = report_tasks.generate_intelligence_report.apply(
        args=[str(report.id), str(run.id)], task_id="report-task"
    ).get()

    db_session.expire_all()
    stored_report = db_session.get(Report, report.id)
    stored_run = db_session.get(AITaskRun, run.id)
    assert result == {"status": "skipped", "reason": "canceled"}
    assert stored_report.status == "skipped"
    assert stored_report.generation_stage == "canceled"
    assert stored_run.status == "skipped"
    assert stored_run.reason == "canceled"
    assert stored_run.error is None


def test_report_task_settles_run_when_report_was_already_completed(
    db_session, monkeypatch
):
    report = _report()
    report.status = "ready"
    report.generation_stage = "ready"
    report.model = "local-threat-model"
    report.model_calls = 4
    report.total_tokens = 120
    run = _task_run(db_session, report)
    _use_test_session(monkeypatch, db_session)
    monkeypatch.setattr(
        report_tasks,
        "generate_report",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("completed reports must not run again")
        ),
    )

    result = report_tasks.generate_intelligence_report.apply(
        args=[str(report.id), str(run.id)], task_id="report-task"
    ).get()

    db_session.expire_all()
    stored_run = db_session.get(AITaskRun, run.id)
    assert result["status"] == "ready"
    assert result["reason"] == "already_completed"
    assert stored_run.status == "ready"
    assert stored_run.total_tokens == 120
    assert stored_run.metadata_json["terminal_report_recovered"] is True


def test_report_task_rejects_mismatched_report_run_without_mutating_run(
    db_session, monkeypatch
):
    report = _report()
    run = _task_run(db_session, report)
    other_report = _report()
    db_session.add(other_report)
    db_session.commit()
    _use_test_session(monkeypatch, db_session)

    result = report_tasks.generate_intelligence_report.apply(
        args=[str(other_report.id), str(run.id)], task_id="report-task"
    ).get()

    db_session.expire_all()
    stored_run = db_session.get(AITaskRun, run.id)
    assert result == {"status": "skipped", "reason": "run_not_available"}
    assert stored_run.status == "queued"
    assert stored_run.started_at is None


def test_schedule_dispatch_defers_without_advancing_when_reporting_unavailable(
    db_session, monkeypatch
):
    _use_test_session(monkeypatch, db_session)
    monkeypatch.setattr(
        report_tasks,
        "load_active_ai_settings",
        lambda _db: SimpleNamespace(
            ai_enabled=False,
            ai_configured=False,
            reporting_enabled=False,
        ),
    )

    result = report_tasks.dispatch_due_report_schedules.run()

    assert result == {
        "status": "deferred",
        "reason": "ai_disabled",
        "queued": 0,
        "failures": 0,
    }


def test_schedule_dispatch_isolates_reservation_failures(db_session, monkeypatch):
    successful_schedule_id = uuid.uuid4()
    failing_schedule_id = uuid.uuid4()
    queued_report = SimpleNamespace(
        id=uuid.uuid4(),
        owner_user_id=uuid.uuid4(),
        status="queued",
    )
    skipped_report = SimpleNamespace(
        id=uuid.uuid4(),
        owner_user_id=uuid.uuid4(),
        status="skipped",
    )
    task_run = SimpleNamespace(id=uuid.uuid4())
    recorded_failures = []
    enqueued = []
    _use_test_session(monkeypatch, db_session)
    monkeypatch.setattr(report_tasks, "load_active_ai_settings", lambda _db: object())
    monkeypatch.setattr(
        report_tasks,
        "ensure_reporting_available",
        lambda _settings: None,
    )
    monkeypatch.setattr(
        report_tasks,
        "list_due_schedule_ids",
        lambda _db, *, now: [successful_schedule_id, failing_schedule_id],
    )

    def reserve(_db, *, schedule_id, now):
        if schedule_id == failing_schedule_id:
            raise RuntimeError("invalid schedule state")
        return [queued_report, skipped_report]

    monkeypatch.setattr(report_tasks, "reserve_schedule_runs", reserve)
    monkeypatch.setattr(
        report_tasks,
        "create_report_task_run",
        lambda *_args, **_kwargs: task_run,
    )
    monkeypatch.setattr(
        report_tasks,
        "record_schedule_failure",
        lambda _db, **kwargs: recorded_failures.append(kwargs),
    )
    monkeypatch.setattr(
        report_tasks,
        "enqueue_report_task",
        lambda **kwargs: enqueued.append(kwargs),
    )

    result = report_tasks.dispatch_due_report_schedules.run()

    assert result == {"status": "partial", "queued": 1, "failures": 1}
    assert enqueued == [{"report_id": queued_report.id, "task_run_id": task_run.id}]
    assert len(recorded_failures) == 1
    assert recorded_failures[0]["schedule_id"] == failing_schedule_id
    assert isinstance(recorded_failures[0]["error"], RuntimeError)
