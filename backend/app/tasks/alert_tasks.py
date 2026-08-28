from __future__ import annotations

import logging
import uuid

from app.services.alert_evaluation import (
    AlertEvaluationLeaseLost,
    claim_alert_evaluation_request,
    evaluate_alert_request,
    record_alert_evaluation_failure,
    record_alert_evaluation_publications,
    record_direct_alert_evaluation_publications,
    release_failed_direct_alert_publications,
    release_alert_evaluation_publications,
    reserve_recoverable_alert_evaluations,
)
from app.services.alert_maintenance import maintain_alert_history
from app.tasks.celery_app import celery_app
from app.tasks.integration_tasks import enqueue_integration_event_routing
from app.tasks.task_session import db_session


logger = logging.getLogger(__name__)


def enqueue_alert_evaluation_requests(request_ids: list[uuid.UUID]) -> bool:
    all_enqueued = True
    failed_request_ids: list[uuid.UUID] = []
    for request_id in request_ids:
        try:
            process_alert_evaluation.delay(str(request_id))
        except Exception as exc:
            all_enqueued = False
            failed_request_ids.append(request_id)
            logger.exception(
                "alert_evaluation_enqueue_failed request_id=%s error_type=%s",
                request_id,
                type(exc).__name__,
            )
    published_request_ids = [
        request_id for request_id in request_ids if request_id not in failed_request_ids
    ]
    if published_request_ids or failed_request_ids:
        try:
            with db_session() as db:
                record_direct_alert_evaluation_publications(
                    db,
                    request_ids=published_request_ids,
                )
                release_failed_direct_alert_publications(
                    db,
                    request_ids=failed_request_ids,
                )
                db.commit()
        except Exception as exc:
            logger.exception(
                "alert_evaluation_enqueue_state_update_failed published_count=%s failed_count=%s error_type=%s",
                len(published_request_ids),
                len(failed_request_ids),
                type(exc).__name__,
            )
    return all_enqueued


@celery_app.task(
    name="app.tasks.alert_tasks.process_alert_evaluation",
    acks_late=True,
    reject_on_worker_lost=True,
)
def process_alert_evaluation(request_id: str):
    parsed_request_id = _parse_uuid(request_id)
    if parsed_request_id is None:
        return {
            "status": "skipped",
            "reason": "invalid_request_id",
            "request_id": request_id,
        }

    with db_session() as db:
        claim = claim_alert_evaluation_request(db, request_id=parsed_request_id)
        db.commit()
        if claim is None:
            return {
                "status": "skipped",
                "reason": "not_claimable",
                "request_id": request_id,
            }

        try:
            outcome = evaluate_alert_request(
                db,
                request_id=parsed_request_id,
                lease_token=claim.lease_token,
            )
            db.commit()
        except AlertEvaluationLeaseLost:
            db.rollback()
            return {
                "status": "skipped",
                "reason": "lease_lost",
                "request_id": request_id,
            }
        except Exception as exc:
            db.rollback()
            failure = record_alert_evaluation_failure(
                db,
                request_id=parsed_request_id,
                lease_token=claim.lease_token,
                error=exc,
            )
            db.commit()
            logger.exception(
                "alert_evaluation_failed request_id=%s error_type=%s error_code=%s state=%s",
                parsed_request_id,
                type(exc).__name__,
                failure.error_code if failure is not None else "evaluation_lease_lost",
                failure.state if failure is not None else "stale",
            )
            return {
                "status": failure.state if failure is not None else "stale",
                "request_id": request_id,
                "error_code": failure.error_code
                if failure is not None
                else "evaluation_lease_lost",
            }

    enqueue_ok = enqueue_integration_event_routing(list(outcome.integration_event_ids))
    return {
        "status": "ok",
        "request_id": request_id,
        "evaluated_rules": outcome.evaluated_rules,
        "occurrences_created": outcome.occurrences_created,
        "suppressed_occurrences": outcome.suppressed_occurrences,
        "notifications_skipped": outcome.notifications_skipped,
        "notification_enqueue_failed": bool(outcome.integration_event_ids)
        and not enqueue_ok,
    }


@celery_app.task(
    name="app.tasks.alert_tasks.dispatch_pending_alert_evaluations",
    acks_late=True,
    reject_on_worker_lost=True,
)
def dispatch_pending_alert_evaluations():
    with db_session() as db:
        reservation = reserve_recoverable_alert_evaluations(db)
        db.commit()

    queued: list[uuid.UUID] = []
    failed: list[uuid.UUID] = []
    for request_id in reservation.request_ids:
        try:
            process_alert_evaluation.delay(str(request_id))
        except Exception as exc:
            failed.append(request_id)
            logger.exception(
                "alert_evaluation_recovery_enqueue_failed request_id=%s error_type=%s",
                request_id,
                type(exc).__name__,
            )
        else:
            queued.append(request_id)
    if queued or failed:
        with db_session() as db:
            record_alert_evaluation_publications(
                db,
                request_ids=queued,
                reserved_at=reservation.reserved_at,
            )
            release_alert_evaluation_publications(
                db,
                request_ids=failed,
                reserved_at=reservation.reserved_at,
            )
            db.commit()
    return {
        "status": "ok",
        "scanned": len(reservation.request_ids),
        "queued": len(queued),
        "enqueue_failed": bool(failed),
    }


@celery_app.task(
    name="app.tasks.alert_tasks.maintain_alert_history",
    acks_late=True,
    reject_on_worker_lost=True,
)
def maintain_alert_history_task():
    with db_session() as db:
        result = maintain_alert_history(db)
    if result.backlog_remaining:
        logger.warning(
            "alert_history_maintenance_backlog stop_reason=%s batches=%s elapsed_ms=%s categories=%s",
            result.stop_reason,
            result.batches_processed,
            result.elapsed_ms,
            ",".join(result.backlog_categories),
        )
    else:
        logger.info(
            "alert_history_maintenance_drained batches=%s elapsed_ms=%s",
            result.batches_processed,
            result.elapsed_ms,
        )
    return {"status": "ok", **result.__dict__}


def _parse_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except (AttributeError, TypeError, ValueError):
        return None
