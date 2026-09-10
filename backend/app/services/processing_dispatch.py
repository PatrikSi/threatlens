"""Bounded discovery and fair publication; PostgreSQL owns work, Redis wakes it."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, case, delete, func, or_, select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.processing_work import (
    ProcessingWork,
    ProcessingRecoveryRun,
    ProcessingRecoveryItem,
    ProcessingDispatchState,
)
from app.services.processing_queries import work_query


def admission_lock(db: Session) -> None:
    db.execute(text("SELECT pg_advisory_xact_lock(1952805744, 9101)"))


def request_work(
    db: Session, row, *, recovery_run_id: uuid.UUID | None = None, force: bool = False
) -> ProcessingWork | None:
    work = db.scalar(
        select(ProcessingWork)
        .where(
            ProcessingWork.item_id == row.item_id,
            ProcessingWork.stage == row.stage,
        )
        .with_for_update()
    )
    if work is not None:
        if work.status == "waiting" and not force:
            work.status = "queued"
            work.version += 1
            return work
        if work.status in {"queued", "running"}:
            return None
        if (
            not force
            and work.source_version == row.source_version
            and work.status != "succeeded"
            and work.status != "cancelled"
            and not (work.reason == "authorization_changed" and work.recovery_run_id)
        ):
            return None
        if (
            not force
            and work.source_version == row.source_version
            and work.status != "succeeded"
            and work.attempts >= get_settings().processing_max_attempts
        ):
            return None
        update_recovery_item(db, work, state="failed", reason="source_changed")
        work.generation += 1
        work.version += 1
        if (
            force
            or work.source_version != row.source_version
            or work.status == "succeeded"
        ):
            work.attempts = 0
    else:
        work = ProcessingWork(
            item_id=row.item_id,
            feed_id=row.feed_id,
            stage=row.stage,
            source_version=row.source_version,
        )
        db.add(work)
    work.source_version = row.source_version
    work.required_since_at = row.required_since_at
    work.feed_id = row.feed_id
    work.status = "queued"
    work.reason = None
    work.claim_token = None
    work.lease_expires_at = None
    work.published_at = None
    work.published_canary_at = None
    work.next_retry_at = None
    work.recovery_run_id = recovery_run_id
    db.flush()
    return work


def discover_processing_work(db: Session, *, stage=None) -> int:
    settings = get_settings()
    admission_lock(db)
    maximum = settings.processing_dispatch_max_in_flight
    reserved = (
        select(ProcessingWork.feed_id, func.count().label("count"))
        .where(
            ProcessingWork.status.in_(("queued", "running", "retry_wait")),
        )
        .group_by(ProcessingWork.feed_id)
        .subquery()
    )
    existing = db.scalar(select(func.coalesce(func.sum(reserved.c.count), 0)))
    allowance = max(0, maximum - existing)
    if not allowance:
        return 0
    rows = work_query(stage=stage)
    now = datetime.now(timezone.utc)
    detached_retry = and_(
        rows.c.domain_pending,
        rows.c.attempts < settings.processing_max_attempts,
        or_(
            rows.c.work_status == "cancelled",
            and_(
                rows.c.work_status == "attention",
                rows.c.work_reason == "authorization_changed",
                rows.c.work_run_id.is_not(None),
            ),
        ),
    )
    ranked = (
        select(
            rows,
            func.row_number()
            .over(
                partition_by=rows.c.feed_id,
                order_by=(rows.c.required_since_at, rows.c.item_id, rows.c.stage),
            )
            .label("feed_rank"),
        )
        .where(
            or_(
                rows.c.work_status == "waiting",
                and_(
                    or_(rows.c.state.in_(("pending", "retry_wait")), detached_retry),
                    or_(rows.c.next_retry_at.is_(None), rows.c.next_retry_at <= now),
                    or_(
                        rows.c.work_id.is_(None),
                        rows.c.work_source_version != rows.c.source_version,
                        rows.c.work_status == "succeeded",
                        detached_retry,
                    ),
                    or_(
                        rows.c.stage != "article",
                        rows.c.article_repair_eligible,
                    ),
                ),
            )
        )
        .subquery()
    )
    candidates = db.execute(
        select(ranked)
        .outerjoin(reserved, reserved.c.feed_id == ranked.c.feed_id)
        .where(
            ranked.c.feed_rank
            <= settings.processing_dispatch_per_feed
            - func.coalesce(reserved.c.count, 0),
        )
        .order_by(
            ranked.c.feed_rank,
            case(
                (
                    ranked.c.feed_id
                    > (
                        db.get(ProcessingDispatchState, 1).last_feed_id
                        or uuid.UUID(int=0)
                    ),
                    0,
                ),
                else_=1,
            ),
            ranked.c.feed_id,
            ranked.c.required_since_at,
            ranked.c.item_id,
            ranked.c.stage,
        )
        .limit(min(allowance, settings.processing_dispatch_batch_size))
    ).all()
    admitted = sum(request_work(db, row) is not None for row in candidates)
    if candidates:
        db.get(ProcessingDispatchState, 1).last_feed_id = candidates[-1].feed_id
    return admitted


def settle_run(db: Session, run_id: uuid.UUID) -> None:
    run = db.scalar(
        select(ProcessingRecoveryRun)
        .where(ProcessingRecoveryRun.id == run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if run is None:
        return
    states = list(
        db.scalars(
            select(ProcessingRecoveryItem.state).where(
                ProcessingRecoveryItem.run_id == run.id
            )
        )
    )
    if run.status != "cancelled":
        if "running" in states:
            run.status = "running"
        elif "queued" in states:
            run.status = (
                "running" if any(value != "queued" for value in states) else "queued"
            )
        elif all(value == "succeeded" for value in states):
            run.status = "succeeded"
        elif any(value == "succeeded" for value in states):
            run.status = "partial"
        else:
            run.status = "failed"
    run.version += 1
    db.add(run)


def update_recovery_item(
    db: Session, work: ProcessingWork, *, state: str, reason: str | None = None
) -> None:
    if work.recovery_run_id is None:
        return
    # Run precedes entry consistently with cancellation. Work locks are skipped
    # by cancellation, so a running transaction cannot form a reversed wait.
    run = db.scalar(
        select(ProcessingRecoveryRun)
        .where(ProcessingRecoveryRun.id == work.recovery_run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    entry = db.get(
        ProcessingRecoveryItem, (work.recovery_run_id, work.item_id, work.stage)
    )
    if run is None or entry is None or entry.generation != work.generation:
        return
    if entry.state in {"succeeded", "failed", "cancelled"}:
        return
    entry.state, entry.reason = state, reason
    if state in {"succeeded", "failed", "cancelled"}:
        # Historical runs retain item identity but do not accumulate FK fanout
        # on the current work row or amplify future Item retention deletes.
        entry.work_id = None
    db.flush()
    settle_run(db, run.id)


def maintain_processing_work(db: Session) -> int:
    # A batch may settle several entries in each of several runs. Serialize
    # maintenance with admission/publication so disjoint SKIP LOCKED Work sets
    # cannot acquire their shared Run rows in opposite orders.
    admission_lock(db)
    now = datetime.now(timezone.utc)
    rows = db.scalars(
        select(ProcessingWork)
        .where(
            ProcessingWork.status == "running",
            ProcessingWork.lease_expires_at <= now,
        )
        .order_by(ProcessingWork.lease_expires_at, ProcessingWork.id)
        .limit(100)
        .with_for_update(skip_locked=True)
    ).all()
    for work in rows:
        fail_work(db, work, reason="worker_interrupted")
    # Deleted source items cascade work, but the principal-owned ledger remains
    # meaningful and must not stay queued forever.
    orphaned = db.execute(
        select(
            ProcessingRecoveryItem.run_id,
            ProcessingRecoveryItem.item_id,
            ProcessingRecoveryItem.stage,
        )
        .where(
            ProcessingRecoveryItem.work_id.is_(None),
            ProcessingRecoveryItem.state.in_(("queued", "running")),
        )
        .order_by(ProcessingRecoveryItem.run_id)
        .limit(100)
    ).all()
    for identity in orphaned:
        db.scalar(
            select(ProcessingRecoveryRun)
            .where(ProcessingRecoveryRun.id == identity.run_id)
            .with_for_update()
        )
        entry = db.get(ProcessingRecoveryItem, tuple(identity), populate_existing=True)
        if entry is None or entry.state not in {"queued", "running"}:
            continue
        entry.state, entry.reason = "failed", "item_deleted"
        db.flush()
        settle_run(db, entry.run_id)
    return len(rows) + len(orphaned)


def prune_recovery_history(db: Session) -> int:
    admission_lock(db)
    cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=get_settings().processing_recovery_retention_seconds
    )
    identities = db.scalars(
        select(ProcessingRecoveryRun.id)
        .where(
            ProcessingRecoveryRun.status.not_in(("queued", "running")),
            ProcessingRecoveryRun.updated_at < cutoff,
        )
        .order_by(ProcessingRecoveryRun.updated_at, ProcessingRecoveryRun.id)
        .limit(100)
        .with_for_update(skip_locked=True)
    ).all()
    if identities:
        # Work-to-run SET NULL would otherwise acquire Work after Run and wait
        # for active attempts. Terminal runs can still have cancellation cleanup
        # in flight, so skip any run retaining queued/running work.
        busy = set(
            db.scalars(
                select(ProcessingWork.recovery_run_id).where(
                    ProcessingWork.recovery_run_id.in_(identities),
                    ProcessingWork.status.in_(("queued", "running")),
                )
            )
        )
        identities = [value for value in identities if value not in busy]
        if identities:
            db.execute(
                delete(ProcessingRecoveryRun).where(
                    ProcessingRecoveryRun.id.in_(identities)
                )
            )
    return len(identities)


def fail_work(
    db: Session, work: ProcessingWork, *, reason: str, retryable: bool = True
) -> None:
    maximum = get_settings().processing_max_attempts
    run = (
        db.scalar(
            select(ProcessingRecoveryRun)
            .where(ProcessingRecoveryRun.id == work.recovery_run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if work.recovery_run_id
        else None
    )
    cancelled = run is not None and run.status == "cancelled"
    retry = not cancelled and retryable and work.attempts < maximum
    work.status = "cancelled" if cancelled else "retry_wait" if retry else "attention"
    work.reason = "cancelled" if cancelled else reason
    work.claim_token = None
    work.lease_expires_at = None
    work.published_at = None
    work.next_retry_at = (
        datetime.now(timezone.utc)
        + timedelta(seconds=min(60 * 2 ** max(0, work.attempts - 1), 900))
        if retry
        else None
    )
    work.version += 1
    update_recovery_item(
        db,
        work,
        state="cancelled" if cancelled else "queued" if retry else "failed",
        reason=work.reason,
    )


def prepare_processing_publications(
    db: Session, *, canary_at: datetime | None, stage=None
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    settings = get_settings()
    admission_lock(db)
    now = datetime.now(timezone.utc)
    maximum = settings.processing_dispatch_max_in_flight
    per_feed = settings.processing_dispatch_per_feed
    active = dict(
        db.execute(
            select(ProcessingWork.feed_id, func.count())
            .where(
                ProcessingWork.status.in_(("queued", "running")),
                ProcessingWork.published_at.is_not(None),
            )
            .group_by(ProcessingWork.feed_id)
        ).all()
    )
    total = sum(active.values())
    eligible = or_(
        and_(ProcessingWork.status == "queued", ProcessingWork.published_at.is_(None)),
        and_(
            ProcessingWork.status == "retry_wait", ProcessingWork.next_retry_at <= now
        ),
    )
    if canary_at is not None:
        eligible = or_(
            eligible,
            and_(
                ProcessingWork.status == "queued",
                ProcessingWork.lease_expires_at <= now,
                ProcessingWork.published_canary_at < canary_at,
            ),
        )
    ranked = select(
        ProcessingWork.id,
        ProcessingWork.feed_id,
        ProcessingWork.updated_at,
        func.row_number()
        .over(
            partition_by=ProcessingWork.feed_id,
            order_by=(ProcessingWork.updated_at, ProcessingWork.id),
        )
        .label("feed_rank"),
    ).where(eligible)
    if stage:
        ranked = ranked.where(ProcessingWork.stage == stage)
    # Last attempt advances the ordering; repeated poison work cannot remain
    # ahead of untouched work from another feed indefinitely.
    ranked = ranked.subquery()
    query = (
        select(ProcessingWork)
        .join(ranked, ranked.c.id == ProcessingWork.id)
        .where(
            ranked.c.feed_rank <= per_feed,
        )
        .order_by(
            ranked.c.feed_rank, ranked.c.updated_at, ranked.c.feed_id, ranked.c.id
        )
        .limit(maximum)
        .with_for_update(of=ProcessingWork, skip_locked=True)
    )
    publications = []
    for work in db.scalars(query):
        already_counted = work.status == "queued" and work.published_at is not None
        if not already_counted and (
            total >= maximum or active.get(work.feed_id, 0) >= per_feed
        ):
            continue
        if work.recovery_run_id:
            run = db.scalar(
                select(ProcessingRecoveryRun)
                .where(ProcessingRecoveryRun.id == work.recovery_run_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if run is None or run.status == "cancelled":
                fail_work(db, work, reason="cancelled", retryable=False)
                continue
        token = uuid.uuid4()
        work.status, work.claim_token = "queued", token
        work.published_at = now
        work.published_canary_at = canary_at or now
        work.lease_expires_at = now + timedelta(
            seconds=settings.processing_claim_lease_seconds
        )
        work.next_retry_at = None
        work.version += 1
        if not already_counted:
            total += 1
            active[work.feed_id] = active.get(work.feed_id, 0) + 1
        publications.append((work.id, token))
        if len(publications) >= settings.processing_dispatch_batch_size:
            break
    return publications
