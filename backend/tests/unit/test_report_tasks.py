import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.models.ai_task_run import AITaskRun
from app.models.report import Report
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
        yield db_session

    monkeypatch.setattr(report_tasks, "db_session", _session)


def test_report_task_skips_redelivery_while_another_lease_is_active(
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

    result = report_tasks.generate_intelligence_report.apply(
        args=[str(report.id), str(run.id)], task_id="report-task"
    ).get()

    assert result == {"status": "skipped", "reason": "already_running"}


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
