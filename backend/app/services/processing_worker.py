"""Execute one claimed source revision and acknowledge it with its domain commit."""

from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db import session as session_module
from app.db.budgets import DatabaseDeadlineExceeded, database_operation
from app.models.article import Article
from app.models.item import Item
from app.models.item_classification import ItemClassification
from app.models.processing_work import ProcessingRecoveryRun, ProcessingWork
from app.services.authorization import AuthorizationStateUnavailable
from app.services.data_access_policy import DataPolicyError
from app.services.export_job_access import ExportJobAccessDenied
from app.services.processing_access import authorize_recovery_run
from app.services.processing_dispatch import fail_work, update_recovery_item
from app.tasks.article_fetch_tasks import run_fetch_article
from app.tasks.feed_task_dependencies import (
    ArticleFetchDependencies,
    ArticleFetchOptions,
    ItemProcessingDependencies,
)
from app.tasks.item_processing_tasks import (
    _reapply_item_tags,
    run_classify_item,
    run_extract_item_iocs,
)

logger = logging.getLogger(__name__)


class ProcessingInterrupted(RuntimeError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class _StageSession(Session):
    """Runners flush at their commit boundary; the recovery unit owns commit.

    This is a distinct session type, never an instance patch or shared mutable
    runner facade. Domain changes and progress cannot become separate commits.
    """

    def commit(self) -> None:
        self.flush()


def _new_stage_session() -> _StageSession:
    return _StageSession(bind=session_module.SessionLocal.kw["bind"], autoflush=False)


def claim_processing_work(work_id: uuid.UUID, token: uuid.UUID) -> bool:
    with (
        session_module.SessionLocal() as db,
        database_operation(db, operation="repair"),
    ):
        work = db.scalar(
            select(ProcessingWork)
            .where(ProcessingWork.id == work_id)
            .with_for_update(skip_locked=True)
        )
        if (
            work is None
            or work.status != "queued"
            or work.claim_token != token
            or work.lease_expires_at is None
            or work.lease_expires_at <= datetime.now(timezone.utc)
        ):
            return False
        if work.recovery_run_id:
            run = db.scalar(
                select(ProcessingRecoveryRun)
                .where(ProcessingRecoveryRun.id == work.recovery_run_id)
                .with_for_update()
            )
            if run is None or run.status == "cancelled":
                fail_work(db, work, reason="cancelled", retryable=False)
                db.commit()
                return False
        work.status, work.attempts, work.version = (
            "running",
            work.attempts + 1,
            work.version + 1,
        )
        work.lease_expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=get_settings().processing_claim_lease_seconds
        )
        update_recovery_item(db, work, state="running")
        db.commit()
        return True


def _locked_attempt(
    db: Session, work_id: uuid.UUID, token: uuid.UUID
) -> ProcessingWork:
    # Read identity first; accepting IAM/handling/owner/credential fences precede
    # Work and Item locks. Cancellation itself needs only current IAM and Run.
    observed = db.get(ProcessingWork, work_id)
    if observed is None:
        raise ProcessingInterrupted("item_deleted")
    run_id = observed.recovery_run_id
    if run_id:
        run = db.get(ProcessingRecoveryRun, run_id)
        if run is None:
            raise ProcessingInterrupted("cancelled")
        authorize_recovery_run(db, run, fence=True)
    work = db.scalar(
        select(ProcessingWork)
        .where(ProcessingWork.id == work_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        work is None
        or work.status != "running"
        or work.claim_token != token
        or work.recovery_run_id != run_id
    ):
        raise ProcessingInterrupted("stale_claim")
    _check_lease(work)
    item = db.scalar(
        select(Item).where(Item.id == work.item_id).with_for_update(skip_locked=True)
    )
    if item is None:
        raise ProcessingInterrupted("busy")
    if item.classification_required_version != work.source_version:
        raise ProcessingInterrupted("source_changed")
    if work.stage == "article" and db.scalar(
        select(
            exists().where(
                Article.item_id == item.id, Article.content_purged_at.is_not(None)
            )
        )
    ):
        raise ProcessingInterrupted("content_purged")
    return work


def _settle_attempt(db: Session, work: ProcessingWork) -> None:
    _check_lease(work)
    if work.recovery_run_id:
        run = db.scalar(
            select(ProcessingRecoveryRun)
            .where(ProcessingRecoveryRun.id == work.recovery_run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if run is None or run.status == "cancelled":
            raise ProcessingInterrupted("cancelled")
        # Policy fences persist through this transaction, but clock-based
        # credential/grant expiry must be checked again before any commit.
        authorize_recovery_run(db, run)
    db.flush()
    item = db.get(Item, work.item_id)
    if work.stage == "article":
        complete = item.status == "content_fetched" and db.scalar(
            select(
                exists().where(
                    Article.item_id == item.id, func.length(func.trim(Article.text)) > 0
                )
            )
        )
    elif work.stage == "classification":
        complete = (
            item.classification_completed_version
            >= item.classification_required_version
            and db.scalar(select(exists().where(ItemClassification.item_id == item.id)))
        )
    elif work.stage == "ioc":
        complete = item.ioc_extraction_state in {"completed", "completed_empty"}
    else:
        complete = not item.tagging_pending
    if complete:
        work.status, work.reason = "succeeded", None
        work.claim_token = work.lease_expires_at = work.next_retry_at = None
        work.version += 1
        update_recovery_item(db, work, state="succeeded")
    else:
        reason = (
            "tagging_incomplete"
            if work.stage == "tagging"
            else "article_failed"
            if work.stage == "article"
            else "worker_failed"
        )
        retryable = work.stage != "tagging" or item.tagging_retry_at is not None
        fail_work(db, work, reason=reason, retryable=retryable)


def _check_lease(work: ProcessingWork) -> None:
    if work.lease_expires_at is None or work.lease_expires_at <= datetime.now(
        timezone.utc
    ):
        raise ProcessingInterrupted("worker_interrupted")


@contextmanager
def _stage_session(work_id: uuid.UUID, token: uuid.UUID, *, stage: str):
    with _new_stage_session() as db:
        budget = (
            database_operation(db, operation="repair")
            if stage != "article"
            else nullcontext()
        )
        with budget:
            work = _locked_attempt(db, work_id, token)
            yield db
            lease_expires_at = work.lease_expires_at
            _settle_attempt(db, work)
            # A settlement that waited on Run must not acknowledge an expired
            # lease merely because maintenance has not reclaimed it yet.
            if lease_expires_at is None or lease_expires_at <= datetime.now(
                timezone.utc
            ):
                raise ProcessingInterrupted("worker_interrupted")
            Session.commit(db)


def _processing_dependencies(factory) -> ItemProcessingDependencies:
    return ItemProcessingDependencies(
        db_session=factory,
        enqueue_iocs=lambda _item: True,
        queue_ai_enrichment=lambda **_kwargs: False,
        record_skipped_ai_enrichment=lambda **_kwargs: uuid.UUID(int=0),
        is_recent_ai_candidate=lambda _item: False,
    )


def _run_stage(stage: str, item_id: uuid.UUID, factory):
    dependencies = _processing_dependencies(factory)
    if stage == "article":
        # Durable claims own retry delays; the legacy fetch runner must not
        # publish an additional Celery retry outside those claims.
        task = SimpleNamespace(request=SimpleNamespace(retries=3))
        return run_fetch_article(
            task,
            str(item_id),
            dependencies=ArticleFetchDependencies(
                db_session=factory,
                settings=ArticleFetchOptions.from_settings(get_settings()),
                enqueue_classification=lambda _item: True,
            ),
        )
    if stage == "classification":
        return run_classify_item(str(item_id), dependencies=dependencies)
    if stage == "ioc":
        return run_extract_item_iocs(str(item_id), dependencies=dependencies)
    if stage == "tagging":
        with factory() as db:
            _reapply_item_tags(db, item_id, dependencies=dependencies)
            db.commit()
        return {"status": "ok"}
    raise ValueError("Unsupported processing stage")


def execute_processing_work(work_id: uuid.UUID, token: uuid.UUID) -> dict[str, str]:
    if not claim_processing_work(work_id, token):
        return {"status": "skipped", "reason": "stale_claim"}
    try:
        with session_module.SessionLocal() as db:
            work = db.get(ProcessingWork, work_id)
            if work is None:
                return {"status": "skipped", "reason": "item_deleted"}
            stage, item_id = work.stage, work.item_id
        _run_stage(stage, item_id, lambda: _stage_session(work_id, token, stage=stage))
    except (ExportJobAccessDenied, AuthorizationStateUnavailable, DataPolicyError):
        _record_failure(work_id, token, "authorization_changed", retryable=False)
    except ProcessingInterrupted as exc:
        _record_failure(
            work_id,
            token,
            exc.reason,
            retryable=exc.reason in {"busy", "worker_interrupted"},
        )
    except DatabaseDeadlineExceeded:
        _record_failure(work_id, token, "database_deadline", retryable=True)
    except Exception as exc:
        logger.warning(
            "processing_attempt_failed work_id=%s error_type=%s",
            work_id,
            type(exc).__name__,
        )
        _record_failure(work_id, token, "worker_failed", retryable=True)
    with session_module.SessionLocal() as db:
        work = db.get(ProcessingWork, work_id)
        return {"status": work.status if work else "skipped"}


def _record_failure(
    work_id: uuid.UUID, token: uuid.UUID, reason: str, *, retryable: bool
):
    with (
        session_module.SessionLocal() as db,
        database_operation(db, operation="repair"),
    ):
        work = db.scalar(
            select(ProcessingWork).where(ProcessingWork.id == work_id).with_for_update()
        )
        if work is not None and work.claim_token == token and work.status == "running":
            fail_work(db, work, reason=reason, retryable=retryable)
            db.commit()
