from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.config import get_settings
from app.models.ai_task_run import AITaskRun
from app.models.integration import IntegrationEvent
from app.models.report import Report
from app.models.report_generation_lease import ReportGenerationLease
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
    ReportGenerationLeaseUnavailableError,
    claim_report_generation,
    fence_report_generation,
    guard_unfenced_report_generation,
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
    supersede_legacy_report_dispatch,
)
from app.services.report_schedules import list_due_schedule_ids, reserve_schedule_runs
from app.services.report_schedules import record_schedule_failure
from app.services.report_task_lineage import find_report_request_task_run
from app.services.report_availability import (
    ReportingUnavailableError,
    ensure_reporting_available,
)
from app.services.ai_config import load_active_ai_settings
from app.tasks.celery_app import QUEUE_AI_REPORTS, celery_app
from app.tasks.integration_tasks import enqueue_integration_event_routing
from app.tasks.task_session import db_session


logger = logging.getLogger(__name__)
settings = get_settings()
REPORT_INFRASTRUCTURE_RETRY_HEADER = "threatlens-report-infrastructure-retry-count"


def create_report_task_run(
    db,
    *,
    report: Report,
    actor_user_id: uuid.UUID | None,
    trigger_source: str,
    originating_request: bool,
    request_idempotency_key_hash: str | None = None,
    request_fingerprint: str | None = None,
):
    if originating_request and report.request_task_run_id is not None:
        raise RuntimeError("The report already has a request task run.")
    if not originating_request and report.request_task_run_id is not None:
        existing_request_run = find_report_request_task_run(db, report=report)
        if existing_request_run is not None:
            report.request_task_run_id = existing_request_run.id
            db.add(report)
    run = queue_ai_task_run(
        db,
        task_type=AI_TASK_TYPE_REPORT,
        trigger_source=trigger_source,
        actor_user_id=actor_user_id,
        report_id=report.id,
        model=report.model,
        metadata={
            "report_id": str(report.id),
            "report_request_origin": originating_request,
            "source_count": report.included_source_count,
            "estimated_input_tokens": report.estimated_input_tokens,
            "estimated_batches": report.generation_batches,
        },
        target_count=report.included_source_count,
    )
    run.request_idempotency_key_hash = request_idempotency_key_hash
    run.request_fingerprint = request_fingerprint
    initialize_report_dispatch(run)
    if originating_request:
        report.request_task_run_id = run.id
        db.add(report)
    return run


def enqueue_report_task(*, report_id: uuid.UUID, task_run_id: uuid.UUID) -> str | None:
    now = datetime.now(timezone.utc)
    try:
        with db_session() as db:
            task_run_id = supersede_legacy_report_dispatch(
                db,
                report_id=report_id,
                task_run_id=task_run_id,
                now=now,
            )
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
    dispatch_token = claim.dispatch_token
    if dispatch_token is None:
        logger.error(
            "report_dispatch_claim_missing_token report_id=%s task_run_id=%s",
            report_id,
            task_run_id,
        )
        return None
    try:
        generate_intelligence_report.apply_async(
            args=[str(report_id), str(task_run_id)],
            queue=QUEUE_AI_REPORTS,
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
                    dispatch_token=dispatch_token,
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
                dispatch_token=dispatch_token,
                celery_task_id=task_id,
                now=datetime.now(timezone.utc),
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
    default_retry_delay=30,
    max_retries=None,
)
def generate_intelligence_report(
    self,
    report_id: str,
    task_run_id: str,
    infrastructure_retry_count: int = 0,
):
    try:
        parsed_report_id = uuid.UUID(report_id)
        parsed_run_id = uuid.UUID(task_run_id)
    except ValueError:
        return {"status": "error", "reason": "invalid_identifier"}
    worker_name = getattr(self.request, "hostname", None)
    celery_task_id = getattr(self.request, "id", None)
    lease_token = uuid.uuid4().hex
    claim = None
    durable_generation_fence = None
    infrastructure_retry_count = _request_infrastructure_retry_count(
        self,
        legacy_value=infrastructure_retry_count,
    )
    try:
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
                started.dispatch_claim_token = None
                started.dispatch_claim_expires_at = None
                db.add(started)
            claimed_by_task = (
                started is not None
                and started.status in {"queued", "running"}
                and started.task_type == AI_TASK_TYPE_REPORT
                and started.report_id == parsed_report_id
                and (celery_task_id is None or started.celery_task_id == celery_task_id)
            )
            report = db.get(Report, parsed_report_id) if claimed_by_task else None
            if claimed_by_task and (
                report is None or report.status in {"ready", "error", "skipped"}
            ):
                terminal_result, event_id = _settle_terminal_report_run(
                    db,
                    report=report,
                    run_id=parsed_run_id,
                    worker_name=worker_name,
                )
                db.commit()
                if event_id is not None:
                    enqueue_integration_event_routing([event_id])
                return terminal_result
            if not claimed_by_task:
                db.rollback()
                return {"status": "skipped", "reason": "run_not_available"}

            claim = claim_report_generation(
                db,
                report_id=parsed_report_id,
                lease_token=lease_token,
                lease_seconds=settings.report_generation_lease_seconds,
                legacy_worker_grace_seconds=settings.report_legacy_worker_grace_seconds,
            )
            if claim.status == "busy":
                if claim.compatibility_guard_created:
                    db.commit()
                else:
                    db.rollback()
            elif claim.status == "unavailable":
                db.rollback()
                return {"status": "skipped", "reason": "run_not_available"}
            elif claim.status == "interrupted":
                # Persist the fencing takeover before settling the interrupted run.
                # A crash between these phases leaves a recoverable active lease;
                # combining them can make a newly inserted lease invisible to the
                # conditional release on some database/session combinations.
                db.commit()
                durable_generation_fence = claim.generation_fence
            else:
                db.commit()
                durable_generation_fence = claim.generation_fence
    except ReportGenerationLeaseLostError as exc:
        logger.warning(
            "report_generation_claim_lost report_id=%s task_run_id=%s error=%s",
            parsed_report_id,
            parsed_run_id,
            exc,
        )
        return {"status": "skipped", "reason": "ownership_lost"}
    except Exception as exc:
        logger.exception(
            "report_generation_start_failed report_id=%s task_run_id=%s",
            parsed_report_id,
            parsed_run_id,
        )
        return _retry_or_settle_report_infrastructure(
            self,
            report_id=parsed_report_id,
            run_id=parsed_run_id,
            worker_name=worker_name,
            lease_token=lease_token,
            generation_fence=durable_generation_fence,
            infrastructure_retry_count=infrastructure_retry_count,
            phase="starting report generation",
            exc=exc,
        )

    if claim.status == "busy":
        raise self.retry(
            countdown=_busy_report_retry_delay(claim.lease_expires_at),
            headers=_report_retry_headers(
                self,
                infrastructure_retry_count=infrastructure_retry_count,
            ),
            kwargs={},
            max_retries=None,
            queue=QUEUE_AI_REPORTS,
        )

    generation_fence = _required_generation_fence(claim)
    if claim.status == "interrupted":
        return _settle_interrupted_generation_task(
            self,
            report_id=parsed_report_id,
            run_id=parsed_run_id,
            worker_name=worker_name,
            lease_token=lease_token,
            generation_fence=generation_fence,
            infrastructure_retry_count=infrastructure_retry_count,
        )

    with db_session() as db:

        def execution_checkpoint() -> None:
            _heartbeat_report_generation(
                report_id=parsed_report_id,
                lease_token=lease_token,
                generation_fence=generation_fence,
            )

        def execution_commit() -> None:
            try:
                owned = fence_report_generation(
                    db,
                    report_id=parsed_report_id,
                    lease_token=lease_token,
                    generation_fence=generation_fence,
                    lease_seconds=settings.report_generation_lease_seconds,
                )
                if not owned:
                    db.rollback()
                    raise ReportGenerationLeaseLostError(
                        "Report execution ownership moved to another worker."
                    )
                db.commit()
            except ReportGenerationLeaseLostError:
                raise
            except Exception as exc:
                db.rollback()
                raise ReportGenerationLeaseUnavailableError(
                    "Report execution ownership could not be committed safely."
                ) from exc

        try:
            result = generate_report(
                db,
                report_id=parsed_report_id,
                task_run_id=parsed_run_id,
                execution_checkpoint=execution_checkpoint,
                execution_commit=execution_commit,
            )
        except ReportGenerationLeaseLostError:
            logger.warning(
                "report_generation_ownership_lost report_id=%s task_run_id=%s",
                parsed_report_id,
                parsed_run_id,
            )
            return {"status": "skipped", "reason": "ownership_lost"}
        except ReportGenerationLeaseUnavailableError as exc:
            logger.warning(
                "report_generation_ownership_unverified report_id=%s task_run_id=%s",
                parsed_report_id,
                parsed_run_id,
            )
            return _retry_or_settle_report_infrastructure(
                self,
                report_id=parsed_report_id,
                run_id=parsed_run_id,
                worker_name=worker_name,
                lease_token=lease_token,
                generation_fence=generation_fence,
                infrastructure_retry_count=infrastructure_retry_count,
                phase="verifying report generation ownership",
                exc=exc,
            )
        except Exception as exc:
            return _settle_failed_generation(
                self,
                db=db,
                report_id=parsed_report_id,
                run_id=parsed_run_id,
                worker_name=worker_name,
                lease_token=lease_token,
                generation_fence=generation_fence,
                infrastructure_retry_count=infrastructure_retry_count,
                exc=exc,
            )

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
        if not release_report_generation(
            db,
            report_id=parsed_report_id,
            lease_token=lease_token,
            generation_fence=generation_fence,
        ):
            db.rollback()
            return {"status": "skipped", "reason": "ownership_lost"}
        try:
            db.commit()
        except Exception as exc:
            db.rollback()
            return _retry_or_settle_report_infrastructure(
                self,
                report_id=parsed_report_id,
                run_id=parsed_run_id,
                worker_name=worker_name,
                lease_token=lease_token,
                generation_fence=generation_fence,
                infrastructure_retry_count=infrastructure_retry_count,
                phase="recording report completion",
                exc=exc,
            )
    notification_enqueued = (
        enqueue_integration_event_routing([event_id]) if event_id else True
    )
    return {
        "status": "ready",
        "report_id": str(parsed_report_id),
        "model_calls": result.model_calls,
        "notification_enqueue_failed": not notification_enqueued,
    }


def _heartbeat_report_generation(
    *,
    report_id: uuid.UUID,
    lease_token: str,
    generation_fence: int,
) -> None:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with db_session() as lease_db:
                owned = renew_report_generation(
                    lease_db,
                    report_id=report_id,
                    lease_token=lease_token,
                    generation_fence=generation_fence,
                    lease_seconds=settings.report_generation_lease_seconds,
                )
                lease_db.commit()
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(0.25 * (attempt + 1))
            continue
        if not owned:
            raise ReportGenerationLeaseLostError(
                "Report execution ownership moved to another worker."
            )
        return
    raise ReportGenerationLeaseUnavailableError(
        "Report execution ownership could not be verified."
    ) from last_error


def _settle_interrupted_report_run(
    db,
    *,
    report_id: uuid.UUID,
    run_id: uuid.UUID,
    worker_name: str | None,
    lease_token: str,
    generation_fence: int,
) -> dict[str, str]:
    report = db.get(Report, report_id)
    if report is None:
        raise ReportGenerationLeaseLostError("The interrupted report no longer exists.")
    report.status = "error"
    report.generation_stage = "failed"
    report.error_code = "generation_interrupted"
    report.error = (
        "The report worker stopped before generation completed. ThreatLens did not "
        "automatically repeat completed AI calls; retry the report to start a fresh attempt."
    )
    db.add(report)
    finish_ai_task_run(
        db,
        run_id=run_id,
        status=AI_STATUS_ERROR,
        reason=report.error_code,
        error=report.error,
        worker_name=worker_name,
        model=report.model,
        prompt_tokens=report.prompt_tokens,
        completion_tokens=report.completion_tokens,
        total_tokens=report.total_tokens,
        metadata_updates={
            "automatic_resume_skipped": True,
            "model_calls": report.model_calls,
        },
        report_id=report_id,
    )
    if not release_report_generation(
        db,
        report_id=report_id,
        lease_token=lease_token,
        generation_fence=generation_fence,
    ):
        db.rollback()
        raise ReportGenerationLeaseLostError(
            "Report execution ownership moved while interruption was being recorded."
        )
    return {"status": "error", "reason": "generation_interrupted"}


def _settle_interrupted_generation_task(
    task,
    *,
    report_id: uuid.UUID,
    run_id: uuid.UUID,
    worker_name: str | None,
    lease_token: str,
    generation_fence: int,
    infrastructure_retry_count: int,
) -> dict[str, str]:
    try:
        with db_session() as db:
            try:
                result = _settle_interrupted_report_run(
                    db,
                    report_id=report_id,
                    run_id=run_id,
                    worker_name=worker_name,
                    lease_token=lease_token,
                    generation_fence=generation_fence,
                )
                db.commit()
            except Exception:
                db.rollback()
                raise
        return result
    except ReportGenerationLeaseLostError as exc:
        logger.warning(
            "report_generation_interruption_settlement_lost "
            "report_id=%s task_run_id=%s error=%s",
            report_id,
            run_id,
            exc,
        )
        return {"status": "skipped", "reason": "ownership_lost"}
    except Exception as exc:
        logger.exception(
            "report_generation_interruption_settlement_failed "
            "report_id=%s task_run_id=%s",
            report_id,
            run_id,
        )
        return _retry_or_settle_report_infrastructure(
            task,
            report_id=report_id,
            run_id=run_id,
            worker_name=worker_name,
            lease_token=lease_token,
            generation_fence=generation_fence,
            infrastructure_retry_count=infrastructure_retry_count,
            phase="recording interrupted report generation",
            exc=exc,
        )


def _settle_failed_generation(
    task,
    *,
    db,
    report_id: uuid.UUID,
    run_id: uuid.UUID,
    worker_name: str | None,
    lease_token: str,
    generation_fence: int,
    infrastructure_retry_count: int,
    exc: Exception,
):
    canceled = getattr(exc, "code", None) == "canceled"
    if canceled:
        logger.info("report_generation_canceled report_id=%s", report_id)
    else:
        logger.exception("report_generation_failed report_id=%s", report_id)
    report = db.get(Report, report_id)
    if report is not None and report.status not in {"ready", "error", "skipped"}:
        report.status = "skipped" if canceled else "error"
        report.generation_stage = "canceled" if canceled else "failed"
        report.error_code = str(getattr(exc, "code", None) or "generation_failed")[:64]
        report.error = _report_error_for_display(exc)
        db.add(report)
    display_error = (
        report.error if report is not None else _report_error_for_display(exc)
    )
    reason = (
        report.error_code
        if report is not None
        else getattr(exc, "code", "generation_failed")
    )
    finish_ai_task_run(
        db,
        run_id=run_id,
        status=AI_STATUS_SKIPPED if canceled else AI_STATUS_ERROR,
        reason=reason,
        error=None if canceled else display_error,
        worker_name=worker_name,
        report_id=report_id,
    )
    if not release_report_generation(
        db,
        report_id=report_id,
        lease_token=lease_token,
        generation_fence=generation_fence,
    ):
        db.rollback()
        return {"status": "skipped", "reason": "ownership_lost"}
    try:
        db.commit()
    except Exception as commit_error:
        db.rollback()
        return _retry_or_settle_report_infrastructure(
            task,
            report_id=report_id,
            run_id=run_id,
            worker_name=worker_name,
            lease_token=lease_token,
            generation_fence=generation_fence,
            infrastructure_retry_count=infrastructure_retry_count,
            phase="recording report generation failure",
            exc=commit_error,
        )
    return {
        "status": "skipped" if canceled else "error",
        "reason": getattr(exc, "code", "generation_failed"),
    }


def _retry_or_settle_report_infrastructure(
    task,
    *,
    report_id: uuid.UUID,
    run_id: uuid.UUID,
    worker_name: str | None,
    lease_token: str,
    generation_fence: int | None,
    infrastructure_retry_count: int,
    phase: str,
    exc: Exception,
):
    retry_count = _coerce_infrastructure_retry_count(infrastructure_retry_count)
    max_retries = settings.report_task_infrastructure_max_retries
    if retry_count < max_retries:
        next_retry_count = retry_count + 1
        countdown = _infrastructure_retry_delay(retry_count)
        retry_headers = _report_retry_headers(
            task,
            infrastructure_retry_count=next_retry_count,
        )
        logger.warning(
            "report_generation_infrastructure_retry report_id=%s task_run_id=%s "
            "phase=%s retry=%s max_retries=%s countdown_seconds=%s error_type=%s",
            report_id,
            run_id,
            phase,
            next_retry_count,
            max_retries,
            countdown,
            type(exc).__name__,
        )
        raise task.retry(
            exc=exc,
            countdown=countdown,
            headers=retry_headers,
            kwargs={},
            max_retries=None,
            queue=QUEUE_AI_REPORTS,
        ) from exc

    logger.error(
        "report_generation_infrastructure_retries_exhausted report_id=%s "
        "task_run_id=%s phase=%s retries=%s error_type=%s",
        report_id,
        run_id,
        phase,
        retry_count,
        type(exc).__name__,
    )
    return _settle_exhausted_report_infrastructure(
        report_id=report_id,
        run_id=run_id,
        worker_name=worker_name,
        lease_token=lease_token,
        generation_fence=generation_fence,
        celery_task_id=getattr(task.request, "id", None),
        retry_count=retry_count,
        phase=phase,
    )


def _settle_exhausted_report_infrastructure(
    *,
    report_id: uuid.UUID,
    run_id: uuid.UUID,
    worker_name: str | None,
    lease_token: str,
    generation_fence: int | None,
    celery_task_id: str | None,
    retry_count: int,
    phase: str,
) -> dict[str, str]:
    error_code = "worker_infrastructure_error"
    error = (
        f"Report generation stopped after {retry_count} infrastructure retries while "
        f"{phase}. ThreatLens did not repeat completed AI calls. Review the AI worker "
        "and database logs, then retry the report."
    )
    try:
        with db_session() as db:
            run = db.scalar(
                select(AITaskRun)
                .where(AITaskRun.id == run_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if (
                run is None
                or run.task_type != AI_TASK_TYPE_REPORT
                or run.report_id != report_id
            ):
                db.rollback()
                return {"status": "skipped", "reason": "run_not_available"}
            if run.finished_at is not None or run.status in {
                AI_STATUS_READY,
                AI_STATUS_ERROR,
                AI_STATUS_SKIPPED,
            }:
                db.rollback()
                return {
                    "status": run.status,
                    "reason": run.reason or "already_settled",
                }
            if run.celery_task_id not in (None, celery_task_id):
                db.rollback()
                logger.error(
                    "report_generation_infrastructure_settlement_deferred "
                    "report_id=%s task_run_id=%s reason=publication_moved",
                    report_id,
                    run_id,
                )
                return {"status": "skipped", "reason": "publication_moved"}
            if celery_task_id is not None and run.celery_task_id is None:
                run.celery_task_id = celery_task_id
                db.add(run)

            report = db.scalar(
                select(Report)
                .where(Report.id == report_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            lease = (
                db.scalar(
                    select(ReportGenerationLease)
                    .where(ReportGenerationLease.report_id == report_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if report is not None
                else None
            )
            if (
                report is not None
                and report.status == "running"
                and not _owns_report_generation_for_settlement(
                    report=report,
                    lease=lease,
                    lease_token=lease_token,
                    generation_fence=generation_fence,
                )
            ):
                guarded = False
                if not _report_has_generation_owner(report=report, lease=lease):
                    guarded = guard_unfenced_report_generation(
                        db,
                        report_id=report_id,
                        grace_seconds=settings.report_legacy_worker_grace_seconds,
                    )
                if guarded:
                    db.commit()
                else:
                    db.rollback()
                logger.error(
                    "report_generation_infrastructure_settlement_deferred "
                    "report_id=%s task_run_id=%s reason=unowned_running_report",
                    report_id,
                    run_id,
                )
                return {
                    "status": "error",
                    "reason": "worker_infrastructure_reconciliation_pending",
                }

            owns_generation = _owns_report_generation_for_settlement(
                report=report,
                lease=lease,
                lease_token=lease_token,
                generation_fence=generation_fence,
            )
            if (
                report is not None
                and report.status in {"queued", "running"}
                and not owns_generation
                and _report_has_generation_owner(report=report, lease=lease)
            ):
                db.rollback()
                logger.error(
                    "report_generation_infrastructure_settlement_deferred "
                    "report_id=%s task_run_id=%s reason=foreign_generation_owner",
                    report_id,
                    run_id,
                )
                return {
                    "status": "error",
                    "reason": "worker_infrastructure_reconciliation_pending",
                }

            release_owned_generation = owns_generation
            event_id = None
            if report is None or report.status in {"ready", "error", "skipped"}:
                result, event_id = _settle_terminal_report_run(
                    db,
                    report=report,
                    run_id=run_id,
                    worker_name=worker_name,
                )
            else:
                finish_ai_task_run(
                    db,
                    run_id=run_id,
                    status=AI_STATUS_ERROR,
                    reason=error_code,
                    error=error,
                    worker_name=worker_name,
                    metadata_updates={
                        "infrastructure_retry_count": retry_count,
                        "infrastructure_failure_phase": phase,
                    },
                    report_id=report_id,
                )
                result = {"status": "error", "reason": error_code}

            if release_owned_generation:
                owned_fence = lease.generation_fence if lease is not None else None
                if owned_fence is None or not release_report_generation(
                    db,
                    report_id=report_id,
                    lease_token=lease_token,
                    generation_fence=owned_fence,
                ):
                    db.rollback()
                    return {"status": "skipped", "reason": "ownership_lost"}
            db.commit()
        if event_id is not None:
            enqueue_integration_event_routing([event_id])
        return result
    except Exception:
        logger.exception(
            "report_generation_infrastructure_terminalization_failed "
            "report_id=%s task_run_id=%s phase=%s",
            report_id,
            run_id,
            phase,
        )
        return {
            "status": "error",
            "reason": "worker_infrastructure_reconciliation_pending",
        }


def _coerce_infrastructure_retry_count(value: object) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def _request_infrastructure_retry_count(task, *, legacy_value: object = 0) -> int:
    headers = getattr(task.request, "headers", None)
    header_value = (
        headers.get(REPORT_INFRASTRUCTURE_RETRY_HEADER)
        if isinstance(headers, dict)
        else None
    )
    return max(
        _coerce_infrastructure_retry_count(header_value),
        _coerce_infrastructure_retry_count(legacy_value),
    )


def _report_retry_headers(
    task,
    *,
    infrastructure_retry_count: int | None = None,
) -> dict[str, object]:
    request_headers = getattr(task.request, "headers", None)
    headers = dict(request_headers) if isinstance(request_headers, dict) else {}
    if infrastructure_retry_count is not None:
        headers[REPORT_INFRASTRUCTURE_RETRY_HEADER] = infrastructure_retry_count
    return headers


def _report_has_generation_owner(
    *,
    report: Report,
    lease: ReportGenerationLease | None,
) -> bool:
    return bool(
        report.generation_lease_token or (lease is not None and lease.lease_token)
    )


def _owns_report_generation_for_settlement(
    *,
    report: Report | None,
    lease: ReportGenerationLease | None,
    lease_token: str,
    generation_fence: int | None,
) -> bool:
    if (
        report is None
        or lease is None
        or report.generation_lease_token != lease_token
        or lease.lease_token != lease_token
    ):
        return False
    return generation_fence is None or lease.generation_fence == generation_fence


def _infrastructure_retry_delay(retry_count: int) -> int:
    exponent = min(30, max(0, retry_count))
    return min(
        settings.report_task_infrastructure_retry_max_backoff_seconds,
        settings.report_task_infrastructure_retry_backoff_seconds * (2**exponent),
    )


def _required_generation_fence(claim) -> int:
    if claim.generation_fence is None:
        raise ReportGenerationLeaseLostError(
            "Report execution ownership did not include a fencing token."
        )
    return claim.generation_fence


def _busy_report_retry_delay(lease_expires_at: datetime | None) -> int:
    if lease_expires_at is None:
        return 15
    expiry = lease_expires_at
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    remaining = max(1, int((expiry - datetime.now(timezone.utc)).total_seconds()))
    return max(5, min(60, remaining // 2 or 1))


@celery_app.task(name="app.tasks.feed_tasks.dispatch_due_report_schedules")
def dispatch_due_report_schedules():
    now = datetime.now(timezone.utc)
    queued = 0
    failures = 0
    with db_session() as db:
        try:
            ensure_reporting_available(load_active_ai_settings(db))
        except ReportingUnavailableError as exc:
            logger.info("scheduled_report_dispatch_deferred reason=%s", exc.code)
            return {
                "status": "deferred",
                "reason": exc.code,
                "queued": 0,
                "failures": 0,
            }
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
