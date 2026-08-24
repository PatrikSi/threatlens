import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event

from sqlalchemy import delete, text
from sqlalchemy.orm import Session, sessionmaker

from app.models.report_schedule import ReportSchedule
from app.models.report_template import ReportTemplate


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
