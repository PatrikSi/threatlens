"""Database aggregates for current source obligations, without article bodies."""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.export_job import ExportJob
from app.models.item import Item
from app.models.processing_work import ProcessingWork
from app.schemas.operations import OperationsBacklogSnapshot
from app.services.operations_common import seconds_since


BACKLOG_LABELS = {
    "integration_deliveries": "Integration deliveries", "reports": "Report generation",
    "classification": "Classification", "tagging": "Automatic tagging", "exports": "Export generation",
}


def load_processing_backlog(
    db: Session, *, stage: str, settings: Settings, now: datetime
) -> OperationsBacklogSnapshot:
    if stage == "classification":
        predicate = Item.classification_completed_version < Item.classification_required_version
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
            func.count(ProcessingWork.id).filter(ProcessingWork.status == "attention"),
        ).join(Item, Item.id == ProcessingWork.item_id).where(
            ProcessingWork.stage == stage, predicate,
            ProcessingWork.source_version == Item.classification_required_version,
        )
    ).one()
    return _snapshot(stage, int(pending), int(active), int(stale), int(failed), oldest, threshold, now)


def load_export_backlog(
    db: Session, *, settings: Settings, now: datetime
) -> OperationsBacklogSnapshot:
    pending, active, stale, failed, oldest = db.execute(select(
        func.count(ExportJob.id).filter(ExportJob.status == "queued"),
        func.count(ExportJob.id).filter(ExportJob.status == "running"),
        func.count(ExportJob.id).filter(ExportJob.status == "running", ExportJob.lease_expires_at < now),
        func.count(ExportJob.id).filter(ExportJob.status == "failed"),
        func.min(ExportJob.created_at).filter(ExportJob.status == "queued"),
    )).one()
    return _snapshot("exports", int(pending), int(active), int(stale), int(failed), oldest, settings.export_freshness_seconds, now)


def _snapshot(
    key: str, pending: int, active: int, stale: int, failed: int,
    oldest: datetime | None, threshold: int, now: datetime,
) -> OperationsBacklogSnapshot:
    age = seconds_since(now, oldest)
    status = "critical" if stale else "degraded" if failed or (age is not None and age >= threshold) else "healthy"
    return OperationsBacklogSnapshot(
        key=key, label=BACKLOG_LABELS[key], status=status,
        pending_count=pending, active_count=active, stale_count=stale,
        failed_count=failed, oldest_pending_age_seconds=age, degraded_after_seconds=threshold,
    )
