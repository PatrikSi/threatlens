import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

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
