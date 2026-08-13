from datetime import datetime, timezone
from types import SimpleNamespace

from app.services.report_schedules import next_schedule_run, schedule_report_period


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

    before_dst = next_schedule_run(schedule, after=datetime(2026, 3, 23, 8, 1, tzinfo=timezone.utc))
    after_dst = next_schedule_run(schedule, after=datetime(2026, 3, 29, 12, 0, tzinfo=timezone.utc))

    assert before_dst == datetime(2026, 3, 30, 7, 0, tzinfo=timezone.utc)
    assert after_dst == datetime(2026, 3, 30, 7, 0, tzinfo=timezone.utc)


def test_monthly_next_run_advances_to_next_month_after_due_time():
    schedule = _schedule(cadence="monthly", day_of_month=15)

    result = next_schedule_run(schedule, after=datetime(2026, 1, 15, 9, 0, tzinfo=timezone.utc))

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
