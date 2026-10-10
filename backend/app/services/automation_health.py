"""Bounded execution maintenance and aggregate monitoring, never external retries."""

from datetime import datetime, timedelta
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.models.automation_execution import AutomationExecution, AutomationPolicyUpdate
from app.models.publication_consumer import PublicationChange, PublicationConsumer, PublicationSubscription
from app.schemas.operations import OperationsBacklogSnapshot
from app.services.operations_common import seconds_since

AUTOMATION_FRESHNESS_SECONDS = 3600
ARCHIVE_AFTER_DAYS = 180
AUTOMATION_BACKLOG_LABELS = {
    "automation_executions": "External execution updates",
    "automation_unknown": "Unknown external outcomes",
    "automation_withdrawals": "Withdrawal acknowledgements",
    "publication_withdrawals": "Publication withdrawal acknowledgements",
    "publication_reconciliation": "Publication reconciliation",
}


def automation_backlog(
    db: Session, *, key: str, now: datetime
) -> OperationsBacklogSnapshot:
    threshold = (
        300 if key == "publication_reconciliation" else AUTOMATION_FRESHNESS_SECONDS
    )
    if key == "publication_withdrawals":
        count, oldest = db.execute(
            select(
                func.count(PublicationChange.id), func.min(PublicationChange.created_at)
            ).where(
                PublicationChange.kind == "withdrawn",
                PublicationChange.acknowledged_at.is_(None),
            )
        ).one()
    elif key == "publication_reconciliation":
        # Successful bounded batches must not hide older subscriptions still
        # waiting for their turn. Attempts alone never advance freshness.
        oldest_subscription = select(func.min(PublicationSubscription.next_check_at)).where(
            PublicationSubscription.consumer_id == PublicationConsumer.id,
            PublicationSubscription.withdrawn_at.is_(None),
        ).correlate(PublicationConsumer).scalar_subquery()
        required = func.least(
            func.coalesce(PublicationConsumer.last_reconciled_at, PublicationConsumer.created_at),
            oldest_subscription,
            PublicationConsumer.reconciliation_error_at,
        )
        count, oldest = db.execute(
            select(func.count(PublicationConsumer.id), func.min(required)).where(
                PublicationConsumer.revoked_at.is_(None),
                PublicationConsumer.expires_at > now,
                required < now - timedelta(seconds=threshold),
            )
        ).one()
    elif key == "automation_withdrawals":
        count, oldest = db.execute(
            select(
                func.count(AutomationPolicyUpdate.id),
                func.min(AutomationPolicyUpdate.created_at),
            ).where(AutomationPolicyUpdate.acknowledged_at.is_(None))
        ).one()
    else:
        statuses = (
            ("unknown",) if key == "automation_unknown" else ("accepted", "running")
        )
        count, oldest = db.execute(
            select(
                func.count(AutomationExecution.id),
                func.min(AutomationExecution.updated_at),
            ).where(AutomationExecution.status.in_(statuses))
        ).one()
    age = seconds_since(now, oldest)
    degraded = bool(count and age is not None and age >= threshold)
    return OperationsBacklogSnapshot(
        key=key,
        label=AUTOMATION_BACKLOG_LABELS[key],
        status="degraded" if degraded else "healthy",
        pending_count=int(count),
        oldest_pending_age_seconds=age,
        degraded_after_seconds=threshold,
    )


def archive_execution_history(db: Session, *, now: datetime, limit: int = 100) -> int:
    """Close cold history only after terminal outcome and final withdrawal ACK.

    Retain the source/evidence and callback digests: neither age nor archival may
    remove external side-effect deduplication or historical investigation facts.
    """
    rows = db.scalars(
        select(AutomationExecution)
        .where(
            AutomationExecution.archived_at.is_(None),
            AutomationExecution.status.in_(("completed", "failed")),
            AutomationExecution.policy_state != "current",
            AutomationExecution.policy_revision
            == AutomationExecution.policy_acknowledged_revision,
            AutomationExecution.updated_at < now - timedelta(days=ARCHIVE_AFTER_DAYS),
            ~select(AutomationPolicyUpdate.id)
            .where(
                AutomationPolicyUpdate.execution_id == AutomationExecution.id,
                (AutomationPolicyUpdate.acknowledged_at.is_(None))
                | (
                    AutomationPolicyUpdate.acknowledged_at
                    >= now - timedelta(days=ARCHIVE_AFTER_DAYS)
                ),
            )
            .exists(),
        )
        .order_by(AutomationExecution.updated_at, AutomationExecution.id)
        .limit(min(max(limit, 1), 100))
        .with_for_update(skip_locked=True)
    ).all()
    for row in rows:
        row.archived_at = now
    return len(rows)
