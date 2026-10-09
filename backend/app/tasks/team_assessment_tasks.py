"""Durable team assessments on the existing bounded AI worker queue."""

import logging
import uuid

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.services import ai_ops
from app.services.ai_execution_ownership import AIExecutionSuperseded, ai_worker_execution
from app.services.ai_provider_client import AIIntegrationError
from app.services.ai_workflow_dispatch import AIWorkflowDeferred, defer_ai_workflow_run
from app.services.team_assessment_generation import generate_team_assessment as generate_assessment
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def _finish_error(db: Session, run_id: uuid.UUID, *, reason: str, error: str) -> dict[str, str]:
    db.rollback()
    run = ai_ops.finish_ai_task_run(db, run_id=run_id, status="error", reason=reason, error=error)
    db.commit()
    if run is None:
        return {"status": "skipped", "reason": "task_not_found"}
    if ai_ops.ai_task_run_stop_reason(run) == "superseded_delivery":
        return {"status": "skipped", "reason": "superseded_delivery"}
    outcome = {"status": run.status}
    if run.reason is not None:
        outcome["reason"] = run.reason
    return outcome


@celery_app.task(name="app.tasks.team_assessment_tasks.generate_team_assessment", bind=True, acks_late=True)
@ai_worker_execution
def generate_team_assessment(self, task_run_id: str, actor_user_id: str | None = None) -> dict[str, str]:
    # actor_user_id is accepted for the common outbox payload; persisted work is
    # the authority. A broker message cannot substitute an actor or credential.
    run_id = uuid.UUID(task_run_id)
    with SessionLocal() as db:
        started = ai_ops.start_ai_task_run(
            db, run_id=run_id, worker_name=getattr(self.request, "hostname", None),
            celery_task_id=getattr(self.request, "id", None),
        )
        can_run = started is not None and started.status == "running" and started.celery_task_id == getattr(self.request, "id", None)
        stop = ai_ops.ai_task_run_stop_reason(started)
        db.commit()
        if not can_run or stop:
            return {"status": "skipped", "reason": stop or "already_running"}
        try:
            generate_assessment(db, run_id=run_id)
        except AIExecutionSuperseded:
            raise
        except AIWorkflowDeferred as exc:
            db.rollback()
            defer_ai_workflow_run(db, run_id=run_id, reason=exc.reason, retry_after_seconds=exc.retry_after_seconds)
            db.commit()
            return {"status": "queued", "reason": exc.reason}
        except AIIntegrationError as exc:
            outcome = _finish_error(db, run_id, reason="assessment_generation_failed", error=str(exc))
            logger.info("team_assessment_failed run_id=%s category=%s", run_id, exc.failure_category)
            return outcome
        except Exception as exc:
            # Never put team context, prompts, provider payloads or credentials in logs.
            logger.error("team_assessment_failed run_id=%s error_type=%s", run_id, type(exc).__name__)
            return _finish_error(db, run_id, reason="unexpected_error", error="Assessment generation could not finish. Retry, or ask an administrator to check this task's request reference.")
    return {"status": "ready"}
