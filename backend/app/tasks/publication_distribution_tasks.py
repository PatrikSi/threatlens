"""Fair bounded reconciliation of opaque publication distribution obligations."""

import logging
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from app.db.budgets import database_operation
from app.db.session import SessionLocal
from app.models.publication_consumer import PublicationConsumer
from app.services.publication_consumers import (
    prune_acknowledged_changes,
    reconcile_consumer,
)
from app.tasks.celery_app import celery_app


@celery_app.task(name="app.tasks.publication_distribution_tasks.reconcile_distribution")
def reconcile_distribution() -> dict[str, int]:
    # Select only identifiers; each consumer uses its own bounded transaction.
    # Attempts drive fairness; only successful work advances freshness.
    with SessionLocal() as db, database_operation(db, operation="repair"):
        ids = list(
            db.scalars(
                select(PublicationConsumer.id)
                .where(
                    PublicationConsumer.revoked_at.is_(None),
                    (
                        PublicationConsumer.last_reconcile_attempt_at.is_(None)
                        | (
                            PublicationConsumer.last_reconcile_attempt_at
                            < datetime.now(timezone.utc) - timedelta(minutes=1)
                        )
                    ),
                )
                .order_by(
                    PublicationConsumer.last_reconcile_attempt_at.asc().nullsfirst(),
                    PublicationConsumer.id,
                )
                .limit(10)
            )
        )
    count = 0
    for identifier in ids:
        with SessionLocal() as db, database_operation(db, operation="repair"):
            row = db.scalar(
                select(PublicationConsumer)
                .where(
                    PublicationConsumer.id == identifier,
                    PublicationConsumer.revoked_at.is_(None),
                    (
                        PublicationConsumer.last_reconcile_attempt_at.is_(None)
                        | (
                            PublicationConsumer.last_reconcile_attempt_at
                            < datetime.now(timezone.utc) - timedelta(minutes=1)
                        )
                    ),
                )
                .with_for_update(skip_locked=True)
            )
            if row is None:
                continue
            attempted_at = datetime.now(timezone.utc)
            row.last_reconcile_attempt_at = attempted_at
            db.commit()
        # Persist the attempt before reconciliation so a malformed retained row
        # cannot monopolize every sweep. Other consumers continue on failure.
        try:
            with SessionLocal() as db, database_operation(db, operation="repair"):
                row = db.scalar(
                    select(PublicationConsumer)
                    .where(
                        PublicationConsumer.id == identifier,
                        PublicationConsumer.revoked_at.is_(None),
                    )
                    .with_for_update(skip_locked=True)
                )
                if row is None:
                    continue
                checked = reconcile_consumer(db, row)
                prune_acknowledged_changes(db, row)
                db.commit()
                count += checked
        except Exception:
            logging.getLogger(__name__).exception(
                "publication_consumer_reconciliation_failed consumer_id=%s", identifier
            )
            # Keep retry fairness independently of success. A concurrent poll
            # may have recovered already; never overwrite newer progress.
            _record_failure(identifier, attempted_at)
    return {"subscriptions_checked": count}


def _record_failure(identifier, attempted_at: datetime) -> None:
    try:
        with SessionLocal() as db, database_operation(db, operation="repair"):
            row = db.scalar(select(PublicationConsumer).where(
                PublicationConsumer.id == identifier,
                PublicationConsumer.last_reconcile_attempt_at == attempted_at,
                (PublicationConsumer.last_reconciled_at.is_(None)
                 | (PublicationConsumer.last_reconciled_at < attempted_at)),
            ).with_for_update(skip_locked=True))
            if row is not None:
                row.reconciliation_error_at = row.reconciliation_error_at or attempted_at
                row.reconciliation_error_code = "publication_reconciliation_failed"
                db.commit()
    except Exception:
        # Freshness already remains stale even if diagnostics cannot be saved.
        logging.getLogger(__name__).exception(
            "publication_consumer_failure_record_failed consumer_id=%s", identifier
        )
