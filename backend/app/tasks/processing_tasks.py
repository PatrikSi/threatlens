"""Repair wake-ups are bounded; durable PostgreSQL claims own delivery/retry."""

import logging
import uuid

from app.core.config import get_settings
from app.core.worker_queues import QUEUE_PROCESSING
from app.db.budgets import database_operation
from app.services.processing_dispatch import (
    discover_processing_work,
    maintain_processing_work,
    prepare_processing_publications,
    prune_recovery_history,
)
from app.services.processing_worker import execute_processing_work
from app.services.queue_execution_canaries import read_queue_execution_canaries
from app.tasks.celery_app import celery_app
from app.tasks.task_session import db_session

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.tasks.processing_tasks.execute_processing_work",
    acks_late=True,
    reject_on_worker_lost=True,
)
def process_work(work_id: str, claim_token: str):
    try:
        identity, token = uuid.UUID(work_id), uuid.UUID(claim_token)
    except ValueError:
        return {"status": "skipped", "reason": "invalid_identity"}
    return execute_processing_work(identity, token)


@celery_app.task(name="app.tasks.processing_tasks.dispatch_processing_work")
def dispatch_processing_work(stage: str | None = None):
    if stage is not None and stage not in {
        "article",
        "classification",
        "ioc",
        "tagging",
    }:
        return {"status": "skipped", "reason": "invalid_stage"}
    canary = read_queue_execution_canaries(
        settings=get_settings(), queues=[QUEUE_PROCESSING]
    ).get(QUEUE_PROCESSING)
    heartbeat = canary.heartbeat_at if canary is not None else None
    with db_session() as db, database_operation(db, operation="repair"):
        recovered = maintain_processing_work(db)
        db.commit()
    with db_session() as db, database_operation(db, operation="repair"):
        pruned = prune_recovery_history(db)
        db.commit()
    with db_session() as db, database_operation(db, operation="repair"):
        discovered = discover_processing_work(db, stage=stage)
        db.commit()
    with db_session() as db, database_operation(db, operation="repair"):
        publications = prepare_processing_publications(
            db, canary_at=heartbeat, stage=stage
        )
        db.commit()
    published = 0
    for work_id, token in publications:
        try:
            process_work.apply_async(args=[str(work_id), str(token)])
            published += 1
        except Exception as exc:
            # Broker acceptance may have succeeded before a connection failed.
            # Preserve the claim; only a later consumer canary permits repair.
            logger.warning(
                "processing_publication_uncertain work_id=%s error_type=%s",
                work_id,
                type(exc).__name__,
            )
    return {
        "discovered": discovered,
        "published": published,
        "recovered": recovered,
        "pruned": pruned,
    }
