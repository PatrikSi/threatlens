import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event

import pytest
from sqlalchemy import delete, text
from sqlalchemy.orm import Session, sessionmaker

from app.models.report_schedule import ReportSchedule
from app.models.report_template import ReportTemplate
from app.services.report_schedules import record_schedule_failure, reserve_schedule_runs


def test_old_schedule_writer_cannot_move_version_backward(database_engine):
    template_id = uuid.uuid4()
    schedule_id = uuid.uuid4()
    session_factory = sessionmaker(
        bind=database_engine,
        autoflush=False,
        autocommit=False,
        class_=Session,
    )
    now = datetime.now(timezone.utc)
    with session_factory.begin() as db:
        db.add(
            ReportTemplate(
                id=template_id,
                name="Concurrent schedule template",
                description="",
                report_type="custom",
                visibility="shared",
                audience="security_team",
                objective="Test schedule version ordering.",
                tone="analytical",
                detail_level="standard",
                use_company_context=False,
                focus_topics_json=[],
                excluded_topics_json=[],
                sections_json=[],
                default_filters_json={},
            )
        )
        db.flush()
        db.add(
            ReportSchedule(
                id=schedule_id,
                template_id=template_id,
                name="Concurrent schedule",
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
                next_run_at=now + timedelta(days=1),
            )
        )

    old_writer_started = Event()
    newer_version = now + timedelta(days=2)
    first = session_factory()
    second = session_factory()
    try:
        first.execute(text("SET LOCAL lock_timeout = '3s'"))
        second.execute(text("SET LOCAL lock_timeout = '3s'"))
        second.execute(text("SELECT now()"))
        second_pid = second.scalar(text("SELECT pg_backend_pid()"))
        assert second_pid is not None
        first.execute(
            text(
                "SELECT id FROM report_schedules WHERE id = :id FOR UPDATE"
            ),
            {"id": schedule_id},
        )
        first.execute(
            text(
                "UPDATE report_schedules "
                "SET name = 'New configuration', updated_at = :version "
                "WHERE id = :id"
            ),
            {"id": schedule_id, "version": newer_version},
        )

        def _old_worker_update():
            old_writer_started.set()
            second.execute(
                text(
                    "UPDATE report_schedules "
                    "SET failure_state = 'retrying', updated_at = now() "
                    "WHERE id = :id"
                ),
                {"id": schedule_id},
            )
            second.commit()

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_old_worker_update)
            assert old_writer_started.wait(timeout=3)
            _wait_for_lock_wait(database_engine, pid=second_pid)
            first.commit()
            future.result(timeout=5)

        with session_factory() as db:
            stored = db.get(ReportSchedule, schedule_id)
            assert stored.name == "New configuration"
            assert stored.failure_state == "retrying"
            assert stored.updated_at > newer_version
    finally:
        first.close()
        second.close()
        with session_factory.begin() as db:
            db.execute(delete(ReportSchedule).where(ReportSchedule.id == schedule_id))
            db.execute(delete(ReportTemplate).where(ReportTemplate.id == template_id))


def _wait_for_lock_wait(database_engine, *, pid: int, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with database_engine.connect() as observer:
            waiting = observer.scalar(
                text(
                    "SELECT wait_event_type = 'Lock' "
                    "FROM pg_stat_activity WHERE pid = :pid"
                ),
                {"pid": pid},
            )
        if waiting:
            return
        time.sleep(0.01)
    raise AssertionError(f"Database session {pid} did not enter a lock wait.")


@pytest.mark.parametrize("operation", ["retry_gate", "reservation_version", "failure_version"])
def test_dispatcher_rechecks_state_after_waiting_for_schedule_lock(database_engine, operation):
    factory = sessionmaker(bind=database_engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    with factory.begin() as db:
        template = ReportTemplate(
            name="Concurrent eligibility", report_type="weekly", visibility="shared",
            audience="security_team", objective="Test eligibility", tone="analytical",
            detail_level="standard", use_company_context=False,
        )
        db.add(template)
        db.flush()
        schedule = ReportSchedule(
            template_id=template.id, name="Concurrent eligibility", enabled=True,
            cadence="weekly", day_of_week=0, hour=9, minute=0, timezone="UTC",
            window_type="previous_complete_week", next_run_at=now - timedelta(minutes=1),
        )
        db.add(schedule)
        db.flush()
        schedule_id, template_id = schedule.id, template.id
    first, second = factory(), factory()
    try:
        # Keep the old identity-map entry alive: reservation must refresh it
        # after obtaining the lock, even with expire_on_commit disabled.
        old_schedule = second.get(ReportSchedule, schedule_id)
        old_version, old_due = old_schedule.updated_at, old_schedule.next_run_at
        second.execute(text("SET LOCAL lock_timeout = '3s'"))
        second_pid = second.scalar(text("SELECT pg_backend_pid()"))
        newer = first.get(ReportSchedule, schedule_id)
        newer.updated_at = now + timedelta(hours=1)
        if operation == "retry_gate":
            newer.retry_at = now + timedelta(minutes=5)
            newer.failure_state = "retrying"
        elif operation == "failure_version":
            newer.next_run_at = now + timedelta(days=7)
        first.flush()

        def stale_dispatcher():
            if operation == "failure_version":
                result = record_schedule_failure(
                    second, schedule_id=schedule_id, now=now, error=RuntimeError("stale failure"),
                    expected_version=old_version, expected_next_run_at=old_due,
                )
            else:
                result = reserve_schedule_runs(
                    second, schedule_id=schedule_id, now=now,
                    expected_version=old_version if operation == "reservation_version" else None,
                )
            second.commit()
            return result

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(stale_dispatcher)
            _wait_for_lock_wait(database_engine, pid=second_pid)
            first.commit()
            assert future.result(timeout=5) == (None if operation == "failure_version" else [])
        with factory() as db:
            stored = db.get(ReportSchedule, schedule_id)
            assert stored.enabled is True
            assert stored.failure_count == 0
            assert stored.updated_at >= now + timedelta(hours=1)
            assert stored.failure_state == ("retrying" if operation == "retry_gate" else "healthy")
    finally:
        first.close()
        second.close()
        with factory.begin() as db:
            db.execute(delete(ReportSchedule).where(ReportSchedule.id == schedule_id))
            db.execute(delete(ReportTemplate).where(ReportTemplate.id == template_id))
