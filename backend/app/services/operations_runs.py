from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.system_operation_run import (
    SYSTEM_OPERATION_STATUSES,
    SYSTEM_OPERATION_TYPES,
    SystemOperationRun,
)
from app.schemas.operations import SystemOperationRunListResponse, SystemOperationRunResponse
from app.services.operations_common import as_utc
from app.services.operations_redaction import (
    MAX_ERROR_MESSAGE_CHARS,
    sanitize_error_code,
    sanitize_identity,
    sanitize_operation_metadata,
    sanitize_source,
    sanitize_text,
)


def create_system_operation_run(
    db: Session,
    *,
    operation_type: str,
    initiated_by: str,
    source: str,
    metadata: dict[str, Any] | None = None,
    started_at: datetime | None = None,
) -> SystemOperationRun:
    """Create an uncommitted ledger row for trusted offline tooling."""
    if operation_type not in SYSTEM_OPERATION_TYPES:
        raise ValueError("Unsupported system operation type")

    run = SystemOperationRun(
        operation_type=operation_type,
        status="running",
        initiated_by=sanitize_identity(initiated_by),
        source=sanitize_source(source),
        metadata_json=sanitize_operation_metadata(metadata or {}),
        started_at=as_utc(started_at or datetime.now(timezone.utc)),
    )
    db.add(run)
    db.flush()
    return run


def finish_system_operation_run(
    db: Session,
    *,
    run_id: uuid.UUID,
    status: str,
    metadata: dict[str, Any] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    finished_at: datetime | None = None,
) -> SystemOperationRun:
    """Finish a run once; repeated identical completion is idempotent."""
    if status not in {"succeeded", "failed"}:
        raise ValueError("System operation completion status must be succeeded or failed")

    run = db.scalar(
        select(SystemOperationRun)
        .where(SystemOperationRun.id == run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if run is None:
        raise ValueError("System operation run not found")
    if run.status != "running":
        if run.status == status:
            return run
        raise ValueError("System operation run is already complete")

    merged_metadata = dict(run.metadata_json or {})
    merged_metadata.update(metadata or {})
    run.metadata_json = sanitize_operation_metadata(merged_metadata)
    completion_time = as_utc(finished_at or datetime.now(timezone.utc))
    if completion_time < as_utc(run.started_at):
        raise ValueError("System operation completion cannot precede its start")
    run.status = status
    run.finished_at = completion_time
    run.error_code = (sanitize_error_code(error_code) or "operation_failed") if status == "failed" else None
    run.error_message = (
        sanitize_text(error_message, max_chars=MAX_ERROR_MESSAGE_CHARS)
        if status == "failed"
        else None
    )
    db.add(run)
    db.flush()
    return run


def list_system_operation_runs(
    db: Session,
    *,
    page: int,
    page_size: int,
    operation_type: str | None = None,
    status: str | None = None,
) -> SystemOperationRunListResponse:
    filters = []
    if operation_type is not None:
        filters.append(SystemOperationRun.operation_type == operation_type)
    if status is not None:
        filters.append(SystemOperationRun.status == status)

    query = select(SystemOperationRun).where(*filters)
    total = int(db.scalar(select(func.count()).select_from(query.subquery())) or 0)
    rows = list(
        db.scalars(
            query.order_by(SystemOperationRun.started_at.desc(), SystemOperationRun.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    )
    return SystemOperationRunListResponse(
        runs=[system_operation_run_response(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


def system_operation_run_response(run: SystemOperationRun) -> SystemOperationRunResponse:
    operation_type = run.operation_type if run.operation_type in SYSTEM_OPERATION_TYPES else "diagnostics"
    status = run.status if run.status in SYSTEM_OPERATION_STATUSES else "failed"
    return SystemOperationRunResponse(
        id=run.id,
        operation_type=operation_type,
        status=status,
        initiated_by=sanitize_identity(run.initiated_by),
        source=sanitize_source(run.source),
        started_at=run.started_at,
        finished_at=run.finished_at,
        created_at=run.created_at,
        updated_at=run.updated_at,
        metadata=sanitize_operation_metadata(run.metadata_json),
        error_code=sanitize_error_code(run.error_code),
        error_message=sanitize_text(run.error_message, max_chars=MAX_ERROR_MESSAGE_CHARS),
    )
