"""Principal-owned, bounded recovery admission, progress and cancellation."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime

from fastapi import Request
from sqlalchemy import func, select, tuple_
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.token_scopes import SCOPE_READ_ITEMS
from app.models.feed import Feed
from app.models.item import Item
from app.models.processing_work import (
    ProcessingRecoveryItem,
    ProcessingRecoveryRun,
    ProcessingWork,
)
from app.schemas.processing import (
    ProcessingRecoveryRequest,
    ProcessingRecoveryResponse,
    ProcessingRecoveryItemResponse,
)
from app.services.authorization import AuthorizationContext, fence_authorization_context
from app.services.data_access_policy import DataAccessContext, fence_data_access_context
from app.services.export_job_access import (
    ExportJobAccessDenied,
    assert_export_sources_visible,
    capture_export_authorization,
    export_source_snapshot,
)
from app.services.export_job_contracts import ExportPrincipal
from app.services.export_principals import export_principal_type
from app.services.processing_access import authorize_recovery_run
from app.services.processing_dispatch import admission_lock, request_work
from app.services.processing_queries import (
    REASONS,
    ProcessingCapacity,
    ProcessingConflict,
    decode_cursor,
    selected_work,
    work_revision,
)
from app.services.secret_storage import encrypt_json

ACTIVE_RUNS = ("queued", "running")


def owned_run(
    db: Session, run_id: uuid.UUID, principal: ExportPrincipal, *, lock=False
) -> ProcessingRecoveryRun | None:
    query = select(ProcessingRecoveryRun).where(
        ProcessingRecoveryRun.id == run_id,
        ProcessingRecoveryRun.principal_type == export_principal_type(principal),
        ProcessingRecoveryRun.principal_id == principal.id,
    )
    if lock:
        query = query.with_for_update()
    return db.scalar(query.execution_options(populate_existing=True))


def accept_recovery(
    db: Session,
    *,
    principal: ExportPrincipal,
    request: Request,
    authorization: AuthorizationContext,
    access: DataAccessContext,
    payload: ProcessingRecoveryRequest,
) -> tuple[ProcessingRecoveryRun, bool]:
    selections = sorted(
        payload.items, key=lambda entry: (entry.item_id.hex, entry.stage)
    )
    if len({(entry.item_id, entry.stage) for entry in selections}) != len(selections):
        raise ProcessingConflict("An item stage may only be selected once.")
    digest = hashlib.sha256(
        json.dumps(
            [entry.model_dump(mode="json") for entry in selections], sort_keys=True
        ).encode()
    ).hexdigest()
    snapshot = capture_export_authorization(request, authorization, access)
    fence_authorization_context(db, authorization)
    fence_data_access_context(db, access)
    admission_lock(db)
    principal_type = export_principal_type(principal)
    prior = db.scalar(
        select(ProcessingRecoveryRun).where(
            ProcessingRecoveryRun.principal_type == principal_type,
            ProcessingRecoveryRun.principal_id == principal.id,
            ProcessingRecoveryRun.idempotency_key == payload.idempotency_key,
        )
    )
    if prior:
        if prior.request_hash != digest:
            raise ProcessingConflict(
                "This idempotency key belongs to a different recovery selection."
            )
        return prior, False
    settings = get_settings()
    own_active = (
        db.scalar(
            select(func.count())
            .select_from(ProcessingRecoveryRun)
            .where(
                ProcessingRecoveryRun.principal_type == principal_type,
                ProcessingRecoveryRun.principal_id == principal.id,
                ProcessingRecoveryRun.status.in_(ACTIVE_RUNS),
            )
        )
        or 0
    )
    retained = db.scalar(select(func.count()).select_from(ProcessingRecoveryRun)) or 0
    if (
        len(selections) > settings.processing_recovery_max_items
        or own_active >= 2
        or retained >= settings.processing_recovery_max_retained
    ):
        raise ProcessingCapacity(
            "Processing recovery capacity is full. Select fewer stages or wait for existing runs to finish or expire."
        )
    run = ProcessingRecoveryRun(
        principal_type=principal_type,
        principal_id=principal.id,
        idempotency_key=payload.idempotency_key,
        request_hash=digest,
        total_count=len(selections),
        authorization_encrypted=encrypt_json(snapshot.model_dump(mode="json")),
        source_encrypted=encrypt_json([]),
    )
    # Owner and credential precede domain rows, as in the worker's final fence.
    authorize_recovery_run(db, run, fence=True)
    # Work rows precede Item locks everywhere, including running stage commits.
    # The admission fence serializes insertion where no Work row exists yet.
    db.scalars(
        select(ProcessingWork)
        .where(ProcessingWork.item_id.in_([entry.item_id for entry in selections]))
        .order_by(ProcessingWork.item_id, ProcessingWork.stage)
        .with_for_update()
    ).all()
    db.scalars(
        select(Item.id)
        .where(Item.id.in_([entry.item_id for entry in selections]))
        .order_by(Item.id)
        .with_for_update()
    ).all()
    rows = []
    for selection in selections:
        row = selected_work(db, selection.item_id, selection.stage)
        if row is None or not access.allows(row.label_id):
            raise ExportJobAccessDenied("Selected processing work is unavailable.")
        if (
            row.state in {"queued", "running"}
            or work_revision(row) != selection.revision
        ):
            raise ProcessingConflict(
                "Selected work changed. Refresh the worklist before retrying."
            )
        rows.append(row)
    run.source_encrypted = encrypt_json(
        export_source_snapshot(db, sorted({row.item_id for row in rows}))
    )
    db.add(run)
    db.flush()
    # Recheck the original credential under owner/credential locks before durable acceptance.
    authorize_recovery_run(db, run, fence=True)
    for row in rows:
        work = request_work(db, row, recovery_run_id=run.id, force=True)
        if work is None:
            raise ProcessingConflict(
                "Selected work was claimed concurrently. Refresh and retry."
            )
        work.status = "waiting"
        db.add(
            ProcessingRecoveryItem(
                run_id=run.id,
                item_id=row.item_id,
                stage=row.stage,
                work_id=work.id,
                generation=work.generation,
            )
        )
    db.flush()
    return run, True


def recovery_response(
    db: Session,
    run: ProcessingRecoveryRun,
    *,
    authorization: AuthorizationContext,
    access: DataAccessContext,
    can_cancel: bool,
) -> ProcessingRecoveryResponse:
    fence_authorization_context(db, authorization)
    fence_data_access_context(db, access)
    permitted = authorization.has(SCOPE_READ_ITEMS)
    try:
        authorize_recovery_run(db, run, fence=True)
        assert_export_sources_visible(db, run, access)
    except ExportJobAccessDenied:
        permitted = False
    entries = []
    if permitted:
        rows = db.execute(
            select(
                ProcessingRecoveryItem,
                func.substr(Item.title, 1, 500),
                func.substr(Feed.name, 1, 255),
            )
            .outerjoin(Item, Item.id == ProcessingRecoveryItem.item_id)
            .outerjoin(Feed, Feed.id == Item.feed_id)
            .where(ProcessingRecoveryItem.run_id == run.id)
            .order_by(ProcessingRecoveryItem.item_id, ProcessingRecoveryItem.stage)
        ).all()
        entries = [
            ProcessingRecoveryItemResponse(
                item_id=entry.item_id,
                stage=entry.stage,
                state=entry.state,
                reason=entry.reason,
                message=REASONS.get(entry.reason),
                title=title,
                feed_name=feed_name,
            )
            for entry, title, feed_name in rows
        ]
        # Expiry is time based; fences alone do not keep a credential unexpired.
        try:
            authorize_recovery_run(db, run)
        except ExportJobAccessDenied:
            permitted, entries = False, []
    return ProcessingRecoveryResponse(
        id=run.id,
        version=run.version,
        status=run.status,
        created_at=run.created_at,
        updated_at=run.updated_at,
        total_count=run.total_count if permitted else 0,
        completed_count=sum(entry.state == "succeeded" for entry in entries),
        failed_count=sum(entry.state == "failed" for entry in entries),
        cancelled_count=sum(entry.state == "cancelled" for entry in entries),
        can_cancel=can_cancel and run.status in ACTIVE_RUNS,
        access_limited=not permitted,
        items=entries,
    )


def cancel_recovery(
    db: Session, run: ProcessingRecoveryRun, *, expected_version: int
) -> bool:
    if run.status == "cancelled":
        return False
    if run.version != expected_version:
        raise ProcessingConflict(
            "Recovery progress changed. Refresh before cancelling."
        )
    if run.status not in ACTIVE_RUNS:
        return False
    run.status, run.version = "cancelled", run.version + 1
    # Running attempts hold Work until their domain transaction completes. Do not
    # wait while holding Run: the worker must acquire Run to validate cancellation.
    work_rows = db.scalars(
        select(ProcessingWork)
        .where(
            ProcessingWork.recovery_run_id == run.id,
            ProcessingWork.status.in_(("waiting", "queued", "running", "retry_wait")),
        )
        .with_for_update(skip_locked=True)
    ).all()
    for work in work_rows:
        work.status, work.reason = "cancelled", "cancelled"
        work.claim_token = work.lease_expires_at = work.next_retry_at = None
        work.version += 1
    entries = db.scalars(
        select(ProcessingRecoveryItem).where(
            ProcessingRecoveryItem.run_id == run.id,
            ProcessingRecoveryItem.state.in_(("queued", "running")),
        )
    ).all()
    for entry in entries:
        entry.state, entry.reason = "cancelled", "cancelled"
        entry.work_id = None
    db.flush()
    return True


def list_owned_runs(
    db: Session, principal: ExportPrincipal, *, limit: int, cursor: str | None
):
    query = select(ProcessingRecoveryRun).where(
        ProcessingRecoveryRun.principal_type == export_principal_type(principal),
        ProcessingRecoveryRun.principal_id == principal.id,
    )
    if cursor:
        raw_time, raw_id = decode_cursor(cursor, length=2)
        try:
            at, identity = datetime.fromisoformat(raw_time), uuid.UUID(raw_id)
            if at.tzinfo is None:
                raise ValueError
        except ValueError as exc:
            raise ProcessingConflict("The result cursor is invalid.") from exc
        query = query.where(
            tuple_(ProcessingRecoveryRun.created_at, ProcessingRecoveryRun.id)
            < tuple_(at, identity)
        )
    return db.scalars(
        query.order_by(
            ProcessingRecoveryRun.created_at.desc(), ProcessingRecoveryRun.id.desc()
        ).limit(limit + 1)
    ).all()
