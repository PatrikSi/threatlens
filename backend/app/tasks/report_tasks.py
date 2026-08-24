from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.config import get_settings
from app.models.integration import IntegrationEvent
from app.models.ai_task_run import AITaskRun
from app.models.report import Report
from app.services.ai_ops import (
    AI_STATUS_ERROR,
    AI_STATUS_READY,
    AI_STATUS_SKIPPED,
    finish_ai_task_run,
    queue_ai_task_run,
    start_ai_task_run,
)
from app.services.ai_ops_common import AI_TASK_TYPE_REPORT
from app.services.report_generation import generate_report
from app.services.report_execution import (
    ReportGenerationLeaseLostError,
    claim_report_generation,
    release_report_generation,
    renew_report_generation,
)
from app.services.report_notifications import REPORT_READY_EVENT_TYPE
from app.services.report_dispatch import (
    claim_report_dispatch,
    initialize_report_dispatch,
    list_due_report_dispatches,
    record_report_dispatch_failure,
    record_report_dispatch_success,
    stable_report_task_id,
)
from app.services.report_schedules import list_due_schedule_ids, reserve_schedule_runs
from app.services.report_schedules import record_schedule_failure
from app.services.report_availability import (
    ReportingUnavailableError,
    ensure_reporting_available,
)
from app.services.ai_config import load_active_ai_settings
from app.tasks.celery_app import celery_app
from app.tasks.integration_tasks import enqueue_integration_event_routing
from app.tasks.task_session import db_session


logger = logging.getLogger(__name__)
settings = get_settings()


def create_report_task_run(
    db,
    *,
    report: Report,
    actor_user_id: uuid.UUID | None,
    trigger_source: str,
    request_idempotency_key_hash: str | None = None,
    request_fingerprint: str | None = None,
):
    run = queue_ai_task_run(
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
    run.request_idempotency_key_hash = request_idempotency_key_hash
    run.request_fingerprint = request_fingerprint
    initialize_report_dispatch(run)
    return run


def enqueue_report_task(*, report_id: uuid.UUID, task_run_id: uuid.UUID) -> str | None:
    now = datetime.now(timezone.utc)
    try:
        with db_session() as db:
            claim = claim_report_dispatch(
                db,
                report_id=report_id,
                task_run_id=task_run_id,
                now=now,
            )
            db.commit()
    except Exception:
        logger.exception(
            "report_dispatch_claim_failed report_id=%s task_run_id=%s",
            report_id,
            task_run_id,
        )
        return None
    if not claim.claimed:
        return claim.celery_task_id

    task_id = stable_report_task_id(task_run_id)
    try:
        generate_intelligence_report.apply_async(
            args=[str(report_id), str(task_run_id)],
            task_id=task_id,
        )
    except Exception:
        logger.exception(
            "report_dispatch_publish_failed report_id=%s task_run_id=%s",
            report_id,
            task_run_id,
        )
        try:
            with db_session() as db:
                record_report_dispatch_failure(
                    db,
                    report_id=report_id,
                    task_run_id=task_run_id,
                    now=datetime.now(timezone.utc),
                )
                db.commit()
        except Exception:
            logger.exception(
                "report_dispatch_failure_record_failed report_id=%s task_run_id=%s",
                report_id,
                task_run_id,
            )
        return None

    try:
        with db_session() as db:
            record_report_dispatch_success(
                db,
                report_id=report_id,
                task_run_id=task_run_id,
                celery_task_id=task_id,
            )
            db.commit()
    except Exception:
        logger.exception(
            "report_dispatch_metadata_update_failed report_id=%s task_run_id=%s",
            report_id,
            task_run_id,
        )
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
    lease_token = uuid.uuid4().hex
    with db_session() as db:
        candidate_run = db.get(AITaskRun, parsed_run_id)
        if (
            candidate_run is None
            or candidate_run.task_type != AI_TASK_TYPE_REPORT
            or candidate_run.report_id != parsed_report_id
        ):
            return {"status": "skipped", "reason": "run_not_available"}
        started = start_ai_task_run(
            db,
            run_id=parsed_run_id,
            worker_name=worker_name,
            celery_task_id=celery_task_id,
            metadata_updates={"report_id": str(parsed_report_id)},
        )
        if started is not None:
            started.dispatch_next_attempt_at = None
            started.dispatch_error = None
            db.add(started)
        claimed_by_task = (
            started is not None
            and started.status in {"queued", "running"}
            and started.task_type == AI_TASK_TYPE_REPORT
            and started.report_id == parsed_report_id
            and (celery_task_id is None or started.celery_task_id == celery_task_id)
        )
        report = db.get(Report, parsed_report_id) if claimed_by_task else None
        if claimed_by_task and (report is None or report.status in {"ready", "error", "skipped"}):
            result = _settle_terminal_report_run(
                db,
                report=report,
                run_id=parsed_run_id,
                worker_name=worker_name,
            )
            db.commit()
            if result[1] is not None:
                enqueue_integration_event_routing([result[1]])
            return result[0]
        claimed_report = claimed_by_task and claim_report_generation(
            db,
            report_id=parsed_report_id,
            lease_token=lease_token,
            lease_seconds=settings.report_generation_lease_seconds,
        )
        db.commit()
        if not claimed_by_task:
            return {"status": "skipped", "reason": "run_not_available"}
        if not claimed_report:
            return {"status": "skipped", "reason": "already_running"}

        def execution_checkpoint() -> None:
            try:
                with db_session() as lease_db:
                    owned = renew_report_generation(
                        lease_db,
                        report_id=parsed_report_id,
                        lease_token=lease_token,
                        lease_seconds=settings.report_generation_lease_seconds,
                    )
                    lease_db.commit()
            except Exception as exc:
                raise ReportGenerationLeaseLostError(
                    "Report execution ownership could not be verified."
                ) from exc
            if not owned:
                raise ReportGenerationLeaseLostError(
                    "Report execution ownership moved to another worker."
                )

        try:
            result = generate_report(
                db,
                report_id=parsed_report_id,
                task_run_id=parsed_run_id,
                execution_checkpoint=execution_checkpoint,
            )
        except ReportGenerationLeaseLostError:
            logger.warning(
                "report_generation_ownership_lost report_id=%s task_run_id=%s",
                parsed_report_id,
                parsed_run_id,
            )
            return {"status": "skipped", "reason": "ownership_lost"}
        except Exception as exc:
            canceled = getattr(exc, "code", None) == "canceled"
            if canceled:
                logger.info("report_generation_canceled report_id=%s", parsed_report_id)
            else:
                logger.exception("report_generation_failed report_id=%s", parsed_report_id)
            report = db.get(Report, parsed_report_id)
            if report is not None and report.status not in {
                "ready",
                "error",
                "skipped",
            }:
                report.status = "skipped" if canceled else "error"
                report.generation_stage = "canceled" if canceled else "failed"
                report.error_code = str(
                    getattr(exc, "code", None) or "generation_failed"
                )[:64]
                report.error = _report_error_for_display(exc)
                db.add(report)
            display_error = (
                report.error
                if report is not None
                else _report_error_for_display(exc)
            )
            finish_ai_task_run(
                db,
                run_id=parsed_run_id,
                status=AI_STATUS_SKIPPED if canceled else AI_STATUS_ERROR,
                reason=report.error_code
                if report is not None
                else getattr(exc, "code", "generation_failed"),
                error=None if canceled else display_error,
                worker_name=worker_name,
                report_id=parsed_report_id,
            )
            release_report_generation(
                db, report_id=parsed_report_id, lease_token=lease_token
            )
            db.commit()
            return {
                "status": "skipped" if canceled else "error",
                "reason": getattr(exc, "code", "generation_failed"),
            }

        finish_ai_task_run(
            db,
            run_id=parsed_run_id,
            status=AI_STATUS_READY,
            worker_name=worker_name,
            model=db.get(Report, parsed_report_id).model
            if db.get(Report, parsed_report_id)
            else None,
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
        release_report_generation(
            db, report_id=parsed_report_id, lease_token=lease_token
        )
        db.commit()
    notification_enqueued = (
        enqueue_integration_event_routing([event_id]) if event_id else True
    )
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
        try:
            ensure_reporting_available(load_active_ai_settings(db))
        except ReportingUnavailableError as exc:
            logger.info(
                "scheduled_report_dispatch_deferred reason=%s", exc.code
            )
            return {"status": "deferred", "reason": exc.code, "queued": 0, "failures": 0}
        schedule_ids = list_due_schedule_ids(db, now=now)
    for schedule_id in schedule_ids:
        try:
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


@celery_app.task(name="app.tasks.feed_tasks.dispatch_pending_report_tasks")
def dispatch_pending_report_tasks():
    now = datetime.now(timezone.utc)
    with db_session() as db:
        entries = list_due_report_dispatches(db, now=now)
    dispatched = 0
    deferred = 0
    for report_id, run_id in entries:
        task_id = enqueue_report_task(report_id=report_id, task_run_id=run_id)
        if task_id:
            dispatched += 1
        else:
            deferred += 1
    return {
        "status": "ok" if deferred == 0 else "partial",
        "dispatched": dispatched,
        "deferred": deferred,
    }


def _report_error_for_display(exc: Exception) -> str:
    from app.services.ai_context_budget import AIContextBudgetError
    from app.services.ai_provider_client import AIIntegrationError
    from app.services.report_generation import ReportGenerationError
    from app.services.report_availability import ReportingUnavailableError

    if isinstance(
        exc,
        (
            AIContextBudgetError,
            AIIntegrationError,
            ReportGenerationError,
            ReportingUnavailableError,
        ),
    ):
        return str(exc)[:4000]
    return "Report generation failed unexpectedly. Review the AI worker logs and retry the report."


def _settle_terminal_report_run(
    db,
    *,
    report: Report | None,
    run_id: uuid.UUID,
    worker_name: str | None,
) -> tuple[dict[str, str], uuid.UUID | None]:
    if report is None:
        finish_ai_task_run(
            db,
            run_id=run_id,
            status=AI_STATUS_SKIPPED,
            reason="report_not_found",
            worker_name=worker_name,
        )
        return {"status": "skipped", "reason": "report_not_found"}, None

    status = {
        "ready": AI_STATUS_READY,
        "error": AI_STATUS_ERROR,
        "skipped": AI_STATUS_SKIPPED,
    }[report.status]
    finish_ai_task_run(
        db,
        run_id=run_id,
        status=status,
        reason=report.error_code,
        error=report.error if status == AI_STATUS_ERROR else None,
        worker_name=worker_name,
        model=report.model,
        prompt_tokens=report.prompt_tokens,
        completion_tokens=report.completion_tokens,
        total_tokens=report.total_tokens,
        metadata_updates={
            "model_calls": report.model_calls,
            "terminal_report_recovered": True,
        },
        report_id=report.id,
    )
    event_id = None
    if report.status == "ready":
        event_id = db.scalar(
            select(IntegrationEvent.id).where(
                IntegrationEvent.event_type == REPORT_READY_EVENT_TYPE,
                IntegrationEvent.source_type == "report",
                IntegrationEvent.source_id == str(report.id),
            )
        )
    return {
        "status": report.status,
        "reason": "already_completed",
        "report_id": str(report.id),
    }, event_id


__all__ = [
    "create_report_task_run",
    "dispatch_due_report_schedules",
    "dispatch_pending_report_tasks",
    "enqueue_report_task",
    "generate_intelligence_report",
]
