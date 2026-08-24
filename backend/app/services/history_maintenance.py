from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.ai_task_run import AITaskRun
from app.models.ai_usage_event import AIUsageEvent
from app.models.audit_log import AuditLog
from app.models.integration import IntegrationRun
from app.models.report import Report
from app.models.tag import TagFeedbackEvent

settings = get_settings()


@dataclass(frozen=True)
class HistoryMaintenanceResult:
    audit_logs_deleted: int
    ai_task_runs_deleted: int
    ai_usage_events_deleted: int
    tag_feedback_events_deleted: int
    integration_runs_deleted: int


def prune_application_history(
    db: Session,
    *,
    now: datetime | None = None,
    batch_size: int | None = None,
) -> HistoryMaintenanceResult:
    current_time = now or datetime.now(timezone.utc)
    effective_batch_size = max(1, int(batch_size or settings.integration_delivery_maintenance_batch_size))
    deleted = HistoryMaintenanceResult(
        audit_logs_deleted=_delete_older_than(
            db,
            AuditLog,
            AuditLog.created_at,
            current_time - timedelta(days=max(1, int(settings.audit_log_retention_days))),
            effective_batch_size,
        ),
        ai_task_runs_deleted=_delete_older_than(
            db,
            AITaskRun,
            AITaskRun.finished_at,
            current_time - timedelta(days=max(1, int(settings.ai_task_history_retention_days))),
            effective_batch_size,
            extra_predicate=and_(
                AITaskRun.finished_at.is_not(None),
                ~select(Report.id)
                .where(
                    Report.request_task_run_id == AITaskRun.id,
                )
                .exists(),
            ),
        ),
        ai_usage_events_deleted=_delete_older_than(
            db,
            AIUsageEvent,
            AIUsageEvent.created_at,
            current_time - timedelta(days=max(1, int(settings.ai_usage_retention_days))),
            effective_batch_size,
        ),
        tag_feedback_events_deleted=_delete_older_than(
            db,
            TagFeedbackEvent,
            TagFeedbackEvent.created_at,
            current_time - timedelta(days=max(1, int(settings.tag_feedback_retention_days))),
            effective_batch_size,
        ),
        integration_runs_deleted=_delete_older_than(
            db,
            IntegrationRun,
            IntegrationRun.finished_at,
            current_time - timedelta(days=max(1, int(settings.integration_run_retention_days))),
            effective_batch_size,
            extra_predicate=IntegrationRun.finished_at.is_not(None),
        ),
    )
    db.commit()
    return deleted


def _delete_older_than(db, model, timestamp_column, cutoff, batch_size, *, extra_predicate=None) -> int:
    query = select(model.id).where(timestamp_column < cutoff)
    if extra_predicate is not None:
        query = query.where(extra_predicate)
    ids = list(db.scalars(query.order_by(timestamp_column.asc(), model.id.asc()).limit(batch_size)).all())
    if not ids:
        return 0
    result = db.execute(delete(model).where(model.id.in_(ids)).execution_options(synchronize_session=False))
    return int(result.rowcount or 0)
