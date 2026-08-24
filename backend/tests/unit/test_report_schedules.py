import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.core.config import get_settings
from app.models.report_schedule import ReportSchedule
from app.models.report_template import ReportTemplate
from app.models.user import User
from app.schemas.reports import ReportArticleFilters
from app.services.ai_context_budget import AIContextBudgetError
from app.services.export_query import ExportSnapshotChangedError
from app.services.report_schedules import (
    apply_schedule_payload,
    list_due_schedule_ids,
    next_schedule_run,
    record_schedule_failure,
    reserve_schedule_runs,
    schedule_report_period,
)
from app.services.report_sources import filters_for_report_period
from app.schemas.reports import ReportScheduleUpdate


def _schedule(**overrides):
    values = {
        "cadence": "weekly",
        "day_of_week": 0,
        "day_of_month": 1,
        "hour": 9,
        "minute": 0,
        "timezone": "Europe/Prague",
        "window_type": "previous_complete_week",
        "rolling_days": 7,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_weekly_next_run_uses_local_timezone_across_dst():
    schedule = _schedule()

    before_dst = next_schedule_run(
        schedule, after=datetime(2026, 3, 23, 8, 1, tzinfo=timezone.utc)
    )
    after_dst = next_schedule_run(
        schedule, after=datetime(2026, 3, 29, 12, 0, tzinfo=timezone.utc)
    )

    assert before_dst == datetime(2026, 3, 30, 7, 0, tzinfo=timezone.utc)
    assert after_dst == datetime(2026, 3, 30, 7, 0, tzinfo=timezone.utc)


def test_monthly_next_run_advances_to_next_month_after_due_time():
    schedule = _schedule(cadence="monthly", day_of_month=15)

    result = next_schedule_run(
        schedule, after=datetime(2026, 1, 15, 9, 0, tzinfo=timezone.utc)
    )

    assert result == datetime(2026, 2, 15, 8, 0, tzinfo=timezone.utc)


def test_previous_complete_week_uses_local_calendar_boundaries():
    schedule = _schedule()

    start, end = schedule_report_period(
        schedule,
        due_at=datetime(2026, 3, 30, 7, 0, tzinfo=timezone.utc),
    )

    assert start == datetime(2026, 3, 22, 23, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 3, 29, 22, 0, tzinfo=timezone.utc)


def test_previous_complete_month_handles_year_boundary():
    schedule = _schedule(cadence="monthly", window_type="previous_complete_month")

    start, end = schedule_report_period(
        schedule,
        due_at=datetime(2026, 1, 5, 8, 0, tzinfo=timezone.utc),
    )

    assert start == datetime(2025, 11, 30, 23, 0, tzinfo=timezone.utc)
    assert end == datetime(2025, 12, 31, 23, 0, tzinfo=timezone.utc)


def test_report_period_filters_preserve_report_filter_contract():
    period_start = datetime(2026, 8, 3, tzinfo=timezone.utc)
    period_end = datetime(2026, 8, 10, tzinfo=timezone.utc)

    result = filters_for_report_period(
        ReportArticleFilters(q="ransomware"),
        period_start=period_start,
        period_end=period_end,
    )

    assert isinstance(result, ReportArticleFilters)
    assert result.q == "ransomware"
    assert result.since == period_start
    assert result.until == period_end


def _persist_schedule(db_session, *, next_run_at: datetime) -> ReportSchedule:
    template = ReportTemplate(
        id=uuid.uuid4(),
        builtin_key=None,
        name=f"Schedule template {uuid.uuid4()}",
        description="",
        report_type="weekly",
        visibility="shared",
        audience="security_team",
        objective="Summarize material security developments.",
        tone="analytical",
        detail_level="standard",
        use_company_context=True,
        focus_topics_json=[],
        excluded_topics_json=[],
        sections_json=[
            {"key": "executive_summary", "title": "Executive Summary", "enabled": True}
        ],
        default_filters_json={},
    )
    schedule = ReportSchedule(
        id=uuid.uuid4(),
        template_id=template.id,
        name=f"Schedule {uuid.uuid4()}",
        enabled=True,
        cadence="weekly",
        day_of_week=0,
        day_of_month=1,
        hour=9,
        minute=0,
        timezone="UTC",
        window_type="previous_complete_week",
        rolling_days=7,
        filters_json={},
        delivery_enabled=False,
        delivery_mode="summary",
        skip_empty=True,
        missed_run_policy="latest",
        next_run_at=next_run_at,
    )
    db_session.add(template)
    db_session.flush()
    db_session.add(schedule)
    db_session.commit()
    return schedule


def test_due_schedule_query_excludes_backed_off_failures(db_session):
    now = datetime.now(timezone.utc)
    backed_off = _persist_schedule(
        db_session, next_run_at=now - timedelta(minutes=5)
    )
    ready = _persist_schedule(db_session, next_run_at=now - timedelta(minutes=1))
    backed_off.failure_state = "retrying"
    backed_off.retry_at = now + timedelta(minutes=5)
    db_session.commit()

    due_ids = list_due_schedule_ids(db_session, now=now)

    assert ready.id in due_ids
    assert backed_off.id not in due_ids


def test_transient_schedule_failure_backs_off_then_advances_occurrence(
    db_session, monkeypatch
):
    monkeypatch.setenv("REPORT_SCHEDULE_MAX_ATTEMPTS", "2")
    get_settings.cache_clear()
    now = datetime.now(timezone.utc)
    schedule = _persist_schedule(
        db_session, next_run_at=now - timedelta(minutes=1)
    )
    error = ExportSnapshotChangedError("snapshot changed")

    record_schedule_failure(
        db_session, schedule_id=schedule.id, now=now, error=error
    )
    db_session.commit()
    db_session.refresh(schedule)
    assert schedule.failure_state == "retrying"
    assert schedule.retry_at == now + timedelta(seconds=60)

    retry_time = schedule.retry_at
    record_schedule_failure(
        db_session, schedule_id=schedule.id, now=retry_time, error=error
    )
    db_session.commit()
    db_session.refresh(schedule)
    assert schedule.failure_state == "exhausted"
    assert schedule.retry_at is None
    assert schedule.consecutive_failure_count == 0
    assert schedule.next_run_at > retry_time
    assert schedule.failure_count == 2


def test_context_budget_failure_eventually_quarantines_schedule(db_session):
    now = datetime.now(timezone.utc)
    schedule = _persist_schedule(
        db_session, next_run_at=now - timedelta(minutes=1)
    )
    error = AIContextBudgetError("Configured report context cannot fit.")

    for attempt in range(3):
        record_schedule_failure(
            db_session,
            schedule_id=schedule.id,
            now=now + timedelta(minutes=attempt),
            error=error,
        )
        db_session.commit()

    db_session.refresh(schedule)
    assert schedule.failure_state == "quarantined"
    assert schedule.enabled is False
    assert schedule.next_run_at is None
    assert schedule.retry_at is None
    assert schedule.last_error_code == "context_budget"


def test_schedule_update_clears_retry_state(db_session):
    now = datetime.now(timezone.utc)
    schedule = _persist_schedule(db_session, next_run_at=now)
    schedule.failure_state = "retrying"
    schedule.consecutive_failure_count = 2
    schedule.retry_at = now + timedelta(hours=1)
    payload = ReportScheduleUpdate(
        template_id=schedule.template_id,
        name=schedule.name,
        enabled=True,
        cadence="weekly",
        day_of_week=0,
        hour=9,
        minute=0,
        timezone="UTC",
        window_type="previous_complete_week",
    )

    apply_schedule_payload(schedule, payload)

    assert schedule.failure_state == "healthy"
    assert schedule.consecutive_failure_count == 0
    assert schedule.retry_at is None


def test_successful_reservation_clears_retry_state(db_session, monkeypatch):
    now = datetime.now(timezone.utc)
    schedule = _persist_schedule(
        db_session, next_run_at=now - timedelta(minutes=1)
    )
    schedule.failure_state = "retrying"
    schedule.consecutive_failure_count = 2
    schedule.retry_at = now
    owner = User(
        id=uuid.uuid4(),
        email=f"schedule-{uuid.uuid4()}@example.com",
        password_hash="not-used-in-this-test",
        role="admin",
        is_active=True,
    )
    db_session.add(owner)
    db_session.flush()
    schedule.owner_user_id = owner.id
    db_session.commit()
    monkeypatch.setattr(
        "app.services.report_schedules._create_one_scheduled_report",
        lambda *_args, **_kwargs: None,
    )

    reports = reserve_schedule_runs(db_session, schedule_id=schedule.id, now=now)

    assert reports == []
    assert schedule.failure_state == "healthy"
    assert schedule.consecutive_failure_count == 0
    assert schedule.retry_at is None
    assert schedule.next_run_at > now
