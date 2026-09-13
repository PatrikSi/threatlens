from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.services.ai_config import load_active_ai_settings
from app.services.report_availability import ReportingUnavailableError, ensure_reporting_available
from app.services.report_schedules import (
    list_due_schedule_candidates,
    record_schedule_failure,
    reserve_schedule_runs,
)
from app.tasks.celery_app import celery_app
from app.tasks.task_session import db_session

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.feed_tasks.dispatch_due_report_schedules")
def dispatch_due_report_schedules():
    from app.tasks.report_tasks import create_report_task_run, enqueue_report_task

    now = datetime.now(timezone.utc)
    queued = 0
    failures = 0
    with db_session() as db:
        try:
            ensure_reporting_available(load_active_ai_settings(db, feature_type="report"))
        except ReportingUnavailableError as exc:
            logger.info("scheduled_report_dispatch_deferred reason=%s", exc.code)
            return {
                "status": "deferred",
                "reason": exc.code,
                "queued": 0,
                "failures": 0,
            }
        candidates = list_due_schedule_candidates(db, now=now)
    for candidate in candidates:
        schedule_id = candidate.schedule_id
        try:
            with db_session() as db:
                reports = reserve_schedule_runs(
                    db, schedule_id=schedule_id, now=now,
                    expected_version=candidate.resource_version,
                )
                queue_entries = []
                for report in reports:
                    if report.status != "queued":
                        continue
                    run = create_report_task_run(
                        db,
                        report=report,
                        actor_user_id=report.owner_user_id,
                        trigger_source="scheduled",
                        originating_request=True,
                    )
                    queue_entries.append((report.id, run.id))
                db.commit()
        except Exception as exc:
            failures += 1
            logger.exception(
                "scheduled_report_reservation_failed schedule_id=%s", schedule_id
            )
            try:
                with db_session() as failure_db:
                    record_schedule_failure(
                        failure_db,
                        schedule_id=schedule_id,
                        now=now,
                        error=exc,
                        expected_version=candidate.resource_version,
                        expected_next_run_at=candidate.due_at,
                    )
                    failure_db.commit()
            except Exception:
                logger.exception(
                    "scheduled_report_failure_state_update_failed schedule_id=%s",
                    schedule_id,
                )
            continue
        for report_id, run_id in queue_entries:
            enqueue_report_task(report_id=report_id, task_run_id=run_id)
            queued += 1
    return {
        "status": "ok" if failures == 0 else "partial",
        "queued": queued,
        "failures": failures,
    }
