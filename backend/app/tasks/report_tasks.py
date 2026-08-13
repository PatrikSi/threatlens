from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.models.integration import IntegrationEvent
from app.models.report import Report
from app.services.ai_ops import (
    AI_STATUS_ERROR,
    AI_STATUS_READY,
    finish_ai_task_run,
    queue_ai_task_run,
    start_ai_task_run,
    update_ai_task_run_celery,
)
from app.services.ai_ops_common import AI_TASK_TYPE_REPORT
from app.services.report_generation import generate_report
from app.services.report_notifications import REPORT_READY_EVENT_TYPE
from app.services.report_schedules import list_due_schedule_ids, reserve_schedule_runs
from app.tasks.celery_app import celery_app
from app.tasks.integration_tasks import enqueue_integration_event_routing
from app.tasks.task_session import db_session


logger = logging.getLogger(__name__)


def create_report_task_run(
    db,
    *,
    report: Report,
    actor_user_id: uuid.UUID | None,
    trigger_source: str,
):
    return queue_ai_task_run(
        db,
        task_type=AI_TASK_TYPE_REPORT,
        trigger_source=trigger_source,
        actor_user_id=actor_user_id,
        report_id=report.id,
        model=report.model,
        metadata={
            "report_id": str(report.id),
            "source_count": report.included_source_count,
            "estimated_input_tokens": report.estimated_input_tokens,
            "estimated_batches": report.generation_batches,
        },
        target_count=report.included_source_count,
    )


def enqueue_report_task(*, report_id: uuid.UUID, task_run_id: uuid.UUID) -> str | None:
    try:
        task = generate_intelligence_report.delay(str(report_id), str(task_run_id))
    except Exception as exc:
        with db_session() as db:
            report = db.get(Report, report_id)
            if report is not None:
                report.status = "error"
                report.generation_stage = "failed"
                report.error_code = "enqueue_failed"
                report.error = "The report worker queue is unavailable. Retry after the queue service recovers."
                db.add(report)
            finish_ai_task_run(
                db,
                run_id=task_run_id,
                status=AI_STATUS_ERROR,
                reason="enqueue_failed",
                error=f"{type(exc).__name__}: {exc}"[:4000],
                worker_name="api",
                report_id=report_id,
            )
            db.commit()
        raise
    task_id = getattr(task, "id", None)
    if task_id:
        with db_session() as db:
            update_ai_task_run_celery(db, run_id=task_run_id, celery_task_id=task_id)
            db.commit()
    return task_id


@celery_app.task(
    bind=True,
    name="app.tasks.feed_tasks.generate_intelligence_report",
    acks_late=True,
    reject_on_worker_lost=True,
)
def generate_intelligence_report(self, report_id: str, task_run_id: str):
    try:
        parsed_report_id = uuid.UUID(report_id)
        parsed_run_id = uuid.UUID(task_run_id)
    except ValueError:
        return {"status": "error", "reason": "invalid_identifier"}
    worker_name = getattr(self.request, "hostname", None)
    celery_task_id = getattr(self.request, "id", None)
    with db_session() as db:
        started = start_ai_task_run(
            db,
            run_id=parsed_run_id,
            worker_name=worker_name,
            celery_task_id=celery_task_id,
            metadata_updates={"report_id": str(parsed_report_id)},
        )
        db.commit()
        if started is None or started.status not in {"queued", "running"}:
            return {"status": "skipped", "reason": "run_not_available"}
        try:
            result = generate_report(db, report_id=parsed_report_id, task_run_id=parsed_run_id)
        except Exception as exc:
            logger.exception("report_generation_failed report_id=%s", parsed_report_id)
            report = db.get(Report, parsed_report_id)
            if report is not None and report.status not in {"ready", "error", "skipped"}:
                report.status = "error"
                report.generation_stage = "failed"
                report.error_code = str(getattr(exc, "code", None) or "generation_failed")[:64]
                report.error = _report_error_for_display(exc)
                db.add(report)
            finish_ai_task_run(
                db,
                run_id=parsed_run_id,
                status=AI_STATUS_ERROR,
                reason=report.error_code if report is not None else getattr(exc, "code", "generation_failed"),
                error=report.error if report is not None else _report_error_for_display(exc),
                worker_name=worker_name,
                report_id=parsed_report_id,
            )
            db.commit()
            return {"status": "error", "reason": getattr(exc, "code", "generation_failed")}

        finish_ai_task_run(
            db,
            run_id=parsed_run_id,
            status=AI_STATUS_READY,
            worker_name=worker_name,
            model=db.get(Report, parsed_report_id).model if db.get(Report, parsed_report_id) else None,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            total_tokens=result.total_tokens,
            metadata_updates={"model_calls": result.model_calls},
            report_id=parsed_report_id,
        )
        event_id = db.scalar(
            select(IntegrationEvent.id).where(
                IntegrationEvent.event_type == REPORT_READY_EVENT_TYPE,
                IntegrationEvent.source_type == "report",
                IntegrationEvent.source_id == str(parsed_report_id),
            )
        )
        db.commit()
    notification_enqueued = enqueue_integration_event_routing([event_id]) if event_id else True
    return {
        "status": "ready",
        "report_id": str(parsed_report_id),
        "model_calls": result.model_calls,
        "notification_enqueue_failed": not notification_enqueued,
    }


@celery_app.task(name="app.tasks.feed_tasks.dispatch_due_report_schedules")
def dispatch_due_report_schedules():
    now = datetime.now(timezone.utc)
    queued = 0
    failures = 0
    with db_session() as db:
        schedule_ids = list_due_schedule_ids(db, now=now)
    for schedule_id in schedule_ids:
        with db_session() as db:
            reports = reserve_schedule_runs(db, schedule_id=schedule_id, now=now)
            queue_entries = []
            for report in reports:
                if report.status != "queued":
                    continue
                run = create_report_task_run(
                    db,
                    report=report,
                    actor_user_id=report.owner_user_id,
                    trigger_source="scheduled",
                )
                queue_entries.append((report.id, run.id))
            db.commit()
        for report_id, run_id in queue_entries:
            try:
                enqueue_report_task(report_id=report_id, task_run_id=run_id)
                queued += 1
            except Exception:
                failures += 1
                logger.exception("scheduled_report_enqueue_failed report_id=%s", report_id)
    return {"status": "ok" if failures == 0 else "partial", "queued": queued, "failures": failures}


def _report_error_for_display(exc: Exception) -> str:
    from app.services.ai_context_budget import AIContextBudgetError
    from app.services.ai_provider_client import AIIntegrationError
    from app.services.report_generation import ReportGenerationError

    if isinstance(exc, (AIContextBudgetError, AIIntegrationError, ReportGenerationError)):
        return str(exc)[:4000]
    return "Report generation failed unexpectedly. Review the AI worker logs and retry the report."


__all__ = [
    "create_report_task_run",
    "dispatch_due_report_schedules",
    "enqueue_report_task",
    "generate_intelligence_report",
]
