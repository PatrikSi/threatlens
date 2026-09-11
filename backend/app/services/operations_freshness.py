"""Database aggregates for current source obligations, without article bodies."""

from datetime import datetime, timedelta

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.export_job import ExportJob
from app.models.item import Item
from app.models.item_classification import ItemClassification
from app.models.processing_work import ProcessingWork
from app.schemas.operations import OperationsBacklogSnapshot
from app.services.operations_common import seconds_since


BACKLOG_LABELS = {
    "integration_deliveries": "Integration deliveries", "reports": "Report generation",
    "classification": "Classification", "tagging": "Automatic tagging", "exports": "Export generation",
}
EXPORT_FAILURE_WINDOW = timedelta(minutes=15)
EXPECTED_EXPORT_FAILURES = (
    "authorization_changed", "size_limit", "empty_export", "snapshot_changed", "owner_deleted",
)


def load_processing_backlog(
    db: Session, *, stage: str, settings: Settings, now: datetime
) -> OperationsBacklogSnapshot:
    if stage == "classification":
        predicate = or_(
            Item.classification_completed_version < Item.classification_required_version,
            ~exists().where(ItemClassification.item_id == Item.id),
        )
        required_at = Item.classification_required_at
        threshold = settings.classification_freshness_seconds
    elif stage == "tagging":
        predicate = Item.tagging_pending.is_(True)
        required_at = Item.tagging_pending_since_at
        threshold = settings.tagging_freshness_seconds
    else:
        raise ValueError("Unsupported processing backlog")
    pending, oldest = db.execute(
        select(func.count(Item.id), func.min(required_at)).where(predicate)
    ).one()
    active, stale, failed = db.execute(
        select(
            func.count(ProcessingWork.id).filter(ProcessingWork.status == "running"),
            func.count(ProcessingWork.id).filter(
                ProcessingWork.status == "running", ProcessingWork.lease_expires_at < now,
            ),
            func.count(ProcessingWork.id).filter(or_(
                ProcessingWork.status == "attention",
                and_(
                    ProcessingWork.status == "cancelled",
                    ProcessingWork.attempts >= settings.processing_max_attempts,
                ),
            )),
        ).join(Item, Item.id == ProcessingWork.item_id).where(
            ProcessingWork.stage == stage, predicate,
            ProcessingWork.source_version == Item.classification_required_version,
        )
    ).one()
    return _snapshot(
        stage, int(pending), int(active), int(stale), int(failed), oldest, threshold, now,
        actionable_failures=int(failed),
    )


def load_export_backlog(
    db: Session, *, settings: Settings, now: datetime
) -> OperationsBacklogSnapshot:
    pending, active, stale, failed, oldest, recent_failed = db.execute(select(
        func.count(ExportJob.id).filter(ExportJob.status == "queued"),
        func.count(ExportJob.id).filter(ExportJob.status == "running"),
        func.count(ExportJob.id).filter(ExportJob.status == "running", ExportJob.lease_expires_at < now),
        func.count(ExportJob.id).filter(ExportJob.status == "failed"),
        func.min(ExportJob.created_at).filter(ExportJob.status == "queued"),
        func.count(ExportJob.id).filter(
            ExportJob.status == "failed",
            or_(ExportJob.error_code.is_(None), ExportJob.error_code.not_in(EXPECTED_EXPORT_FAILURES)),
            func.coalesce(ExportJob.completed_at, ExportJob.created_at) >= now - EXPORT_FAILURE_WINDOW,
        ),
    )).one()
    return _snapshot(
        "exports", int(pending), int(active), int(stale), int(failed), oldest,
        settings.export_freshness_seconds, now, actionable_failures=int(recent_failed),
    )


def _snapshot(
    key: str, pending: int, active: int, stale: int, failed: int,
    oldest: datetime | None, threshold: int, now: datetime, *, actionable_failures: int,
) -> OperationsBacklogSnapshot:
    age = seconds_since(now, oldest)
    status = "critical" if stale else "degraded" if actionable_failures or (age is not None and age >= threshold) else "healthy"
    return OperationsBacklogSnapshot(
        key=key, label=BACKLOG_LABELS[key], status=status,
        pending_count=pending, active_count=active, stale_count=stale,
        failed_count=failed, oldest_pending_age_seconds=age, degraded_after_seconds=threshold,
    )
