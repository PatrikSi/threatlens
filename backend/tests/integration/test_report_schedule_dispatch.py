import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import sessionmaker

from app.models.ai_task_run import AITaskRun
from app.models.data_policy import DataAccessEnvelope
from app.models.report import Report
from app.models.report_schedule import ReportSchedule
from app.models.report_template import ReportTemplate
from app.models.user import User
from app.services import report_schedules
from app.tasks import report_schedule_tasks, report_tasks


NOW = datetime(2026, 9, 7, 9, 1, tzinfo=timezone.utc)
DUE = NOW - timedelta(minutes=1)


@pytest.fixture
def dispatch_fixture(database_engine, monkeypatch):
    factory = sessionmaker(bind=database_engine, expire_on_commit=False)
    owner_id, template_id = uuid.uuid4(), uuid.uuid4()
    with factory.begin() as db:
        db.add(User(id=owner_id, email=f"dispatch-{owner_id}@example.com", password_hash="unused"))
        db.add(ReportTemplate(
            id=template_id, name="Dispatch transaction fixture", report_type="weekly",
            visibility="shared", audience="security_team", objective="Test dispatch",
            tone="analytical", detail_level="standard", use_company_context=False,
        ))

    def add_schedule(name, *, due=DUE):
        with factory.begin() as db:
            schedule = ReportSchedule(
                owner_user_id=owner_id, template_id=template_id, name=name,
                enabled=True, cadence="weekly", day_of_week=0, hour=9, minute=0,
                timezone="UTC", window_type="previous_complete_week",
                missed_run_policy="skip", next_run_at=due,
            )
            db.add(schedule)
            db.flush()
            return schedule.id

    # Isolate report content planning: reservation, versioning, task-run creation,
    # transaction boundaries and broker-dispatch settlement all remain real.
    def create_report(db, *, schedule, template, due_at, **_kwargs):
        report = Report(
            owner_user_id=owner_id, template_id=template.id, schedule_id=schedule.id,
            title=schedule.name, report_type="weekly", status="queued",
            trigger_source="scheduled", generation_stage="queued",
            generation_key=f"schedule:{schedule.id}:{due_at.isoformat()}",
            period_start=due_at - timedelta(days=7), period_end=due_at,
        )
        db.add(report)
        db.flush()
        return report

    monkeypatch.setattr(report_schedules, "_create_one_scheduled_report", create_report)
    monkeypatch.setattr(report_schedule_tasks, "datetime", SimpleNamespace(now=lambda _tz: NOW))
    monkeypatch.setattr(report_schedule_tasks, "load_active_ai_settings", lambda _db: SimpleNamespace(
        ai_enabled=True, ai_configured=True, reporting_enabled=True,
    ))
    try:
        yield SimpleNamespace(factory=factory, add_schedule=add_schedule, create_report=create_report)
    finally:
        with factory.begin() as db:
            run_ids = list(db.scalars(select(AITaskRun.id).where(AITaskRun.actor_user_id == owner_id)))
            report_ids = list(db.scalars(select(Report.id).where(Report.owner_user_id == owner_id)))
            db.execute(delete(AITaskRun).where(AITaskRun.id.in_(run_ids)))
            db.execute(delete(Report).where(Report.id.in_(report_ids)))
            db.execute(delete(DataAccessEnvelope).where(DataAccessEnvelope.resource_id.in_(run_ids + report_ids)))
            db.execute(delete(ReportSchedule).where(ReportSchedule.owner_user_id == owner_id))
            db.execute(delete(ReportTemplate).where(ReportTemplate.id == template_id))
            db.execute(delete(User).where(User.id == owner_id))


@pytest.mark.parametrize("broker_available", [True, False])
def test_schedule_dispatch_commits_recoverable_work_before_publishing(
    dispatch_fixture, monkeypatch, broker_available,
):
    fixture = dispatch_fixture
    schedule_id = fixture.add_schedule("Successful schedule")
    published = []

    def publish(*, args, queue, task_id):
        report_id, run_id = map(uuid.UUID, args)
        # A separate database connection must see the report and run already
        # committed when the broker receives the message.
        with fixture.factory() as observer:
            report = observer.get(Report, report_id)
            run = observer.get(AITaskRun, run_id)
            schedule = observer.get(ReportSchedule, schedule_id)
            assert report.request_task_run_id == run.id
            assert run.report_id == report.id
            assert run.trigger_source == "scheduled"
            assert run.dispatch_claim_token is not None
            assert schedule.next_run_at == DUE + timedelta(days=7)
        assert queue == report_tasks.QUEUE_AI_REPORTS
        published.append((report_id, run_id, task_id))
        if not broker_available:
            raise ConnectionError("Isolated broker failure")

    monkeypatch.setattr(report_tasks.generate_intelligence_report, "apply_async", publish)

    assert report_schedule_tasks.dispatch_due_report_schedules.run() == {
        "status": "ok", "queued": 1, "failures": 0,
    }
    assert len(published) == 1
    report_id, run_id, task_id = published[0]
    with fixture.factory() as db:
        run = db.get(AITaskRun, run_id)
        assert db.get(Report, report_id).status == "queued"
        assert run.status == "queued"
        assert run.dispatch_claim_token is None
        if broker_available:
            assert run.dispatch_attempt_count == 0
            assert run.celery_task_id == task_id
            assert run.dispatch_published_at is not None
            assert run.dispatch_error is None
        else:
            assert run.dispatch_attempt_count == 1
            assert run.dispatch_published_at is None
            assert run.dispatch_error is not None
            assert run.dispatch_next_attempt_at is not None
    assert report_schedule_tasks.dispatch_due_report_schedules.run() == {
        "status": "ok", "queued": 0, "failures": 0,
    }
    assert len(published) == 1


@pytest.mark.parametrize("settlement_available", [True, False])
def test_schedule_dispatch_rolls_back_failed_reservation_and_continues(
    dispatch_fixture, monkeypatch, settlement_available,
):
    fixture = dispatch_fixture
    failing_id = fixture.add_schedule("Failing schedule", due=DUE - timedelta(minutes=1))
    healthy_id = fixture.add_schedule("Healthy schedule")
    published = []

    def prepare(db, **kwargs):
        report = fixture.create_report(db, **kwargs)
        if kwargs["schedule"].id == failing_id:
            raise RuntimeError("Failure after inserting the draft report")
        return report

    monkeypatch.setattr(report_schedules, "_create_one_scheduled_report", prepare)
    if not settlement_available:
        def fail_settlement(*_args, **_kwargs):
            raise ConnectionError("Failure-state database unavailable")

        monkeypatch.setattr(report_schedule_tasks, "record_schedule_failure", fail_settlement)
    monkeypatch.setattr(
        report_tasks.generate_intelligence_report, "apply_async",
        lambda **kwargs: published.append(kwargs),
    )

    assert report_schedule_tasks.dispatch_due_report_schedules.run() == {
        "status": "partial", "queued": 1, "failures": 1,
    }
    assert len(published) == 1
    with fixture.factory() as db:
        assert db.scalar(select(Report.id).where(Report.schedule_id == failing_id)) is None
        healthy_report = db.scalar(select(Report).where(Report.schedule_id == healthy_id))
        assert healthy_report.request_task_run_id is not None
        failed = db.get(ReportSchedule, failing_id)
        assert failed.next_run_at == DUE - timedelta(minutes=1)
        assert failed.last_run_at is None
        assert failed.failure_count == (1 if settlement_available else 0)
        assert failed.failure_state == ("retrying" if settlement_available else "healthy")
        if settlement_available:
            assert failed.retry_at > NOW
        assert db.get(ReportSchedule, healthy_id).next_run_at == DUE + timedelta(days=7)
