from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass

from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.ai_task_run import AITaskRun
from app.models.report import Report
from app.models.report_operation_receipt import ReportOperationReceipt
from app.services.report_task_lineage import (
    ReportTaskLineageError,
    find_report_request_task_run,
    resolve_report_task_run,
)


MAX_IDEMPOTENCY_KEY_LENGTH = 255


class ReportIdempotencyError(ValueError):
    pass


class ReportIdempotencyConflictError(ReportIdempotencyError):
    pass


@dataclass(frozen=True)
class ReportRequestIdentity:
    legacy_key: str
    key_hash: str
    fingerprint: str


def build_report_create_identity(
    key: str | None,
    *,
    payload: BaseModel,
) -> ReportRequestIdentity | None:
    normalized = _normalize_key(key)
    if normalized is None:
        return None
    return ReportRequestIdentity(
        legacy_key=normalized,
        key_hash=_sha256(f"report:create\0{normalized}"),
        fingerprint=_payload_fingerprint(payload.model_dump(mode="json")),
    )


def build_report_retry_identity(
    key: str | None,
    *,
    report_id: uuid.UUID,
) -> ReportRequestIdentity | None:
    normalized = _normalize_key(key)
    if normalized is None:
        return None
    scope = f"report:retry:{report_id}"
    return ReportRequestIdentity(
        legacy_key=normalized,
        key_hash=_sha256(f"{scope}\0{normalized}"),
        fingerprint=_payload_fingerprint(
            {"operation": "retry", "report_id": str(report_id), "version": 1}
        ),
    )


def build_report_schedule_run_identity(
    key: str | None,
    *,
    schedule_id: uuid.UUID,
    actor_user_id: uuid.UUID,
) -> ReportRequestIdentity | None:
    normalized = _normalize_key(key)
    if normalized is None:
        return None
    scope = f"report:schedule-run:{schedule_id}:{actor_user_id}"
    return ReportRequestIdentity(
        legacy_key=normalized,
        key_hash=_sha256(f"{scope}\0{normalized}"),
        fingerprint=_payload_fingerprint(
            {
                "operation": "schedule_run",
                "schedule_id": str(schedule_id),
                "actor_user_id": str(actor_user_id),
                "version": 1,
            }
        ),
    )


def build_report_operation_identity(
    key: str | None,
    *,
    operation: str,
    payload: BaseModel | object,
) -> ReportRequestIdentity | None:
    normalized = _normalize_key(key)
    if normalized is None:
        return None
    payload_value = (
        payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
    )
    return ReportRequestIdentity(
        legacy_key=normalized,
        key_hash=_sha256(f"{operation}\0{normalized}"),
        fingerprint=_payload_fingerprint(payload_value),
    )


def find_report_operation_replay(
    db: Session,
    *,
    user_id: uuid.UUID,
    operation: str,
    resource_type: str,
    identity: ReportRequestIdentity | None,
) -> ReportOperationReceipt | None:
    if identity is None:
        return None
    receipt = db.scalar(
        select(ReportOperationReceipt).where(
            ReportOperationReceipt.actor_user_id == user_id,
            ReportOperationReceipt.key_hash == identity.key_hash,
        )
    )
    if receipt is None:
        return None
    if receipt.operation != operation or receipt.resource_type != resource_type:
        raise ReportIdempotencyConflictError(
            "The Idempotency-Key is already associated with another reporting operation."
        )
    _ensure_matching_fingerprint(
        receipt.fingerprint,
        identity.fingerprint,
        conflict_message=(
            "The Idempotency-Key was already used with different data for this "
            "reporting operation."
        ),
    )
    return receipt


def record_report_operation_receipt(
    db: Session,
    *,
    user_id: uuid.UUID,
    operation: str,
    resource_type: str,
    resource_id: uuid.UUID,
    identity: ReportRequestIdentity | None,
) -> None:
    if identity is None:
        return
    db.add(
        ReportOperationReceipt(
            actor_user_id=user_id,
            operation=operation,
            key_hash=identity.key_hash,
            fingerprint=identity.fingerprint,
            resource_type=resource_type,
            resource_id=resource_id,
        )
    )


def find_report_create_replay(
    db: Session,
    *,
    user_id: uuid.UUID,
    identity: ReportRequestIdentity | None,
) -> tuple[Report, AITaskRun] | None:
    if identity is None:
        return None
    report = db.scalar(
        select(Report).where(
            Report.owner_user_id == user_id,
            or_(
                Report.request_idempotency_key_hash == identity.key_hash,
                Report.request_idempotency_key == identity.legacy_key,
            ),
        )
    )
    if report is None:
        return None
    _ensure_matching_fingerprint(report.request_fingerprint, identity.fingerprint)
    run = _request_report_task_run(db, report=report)
    if run is None:
        raise ReportIdempotencyConflictError(
            "The original report request exists, but its task record is unavailable. "
            "Use a new Idempotency-Key to create another report."
        )
    return report, run


def find_report_retry_replay(
    db: Session,
    *,
    user_id: uuid.UUID,
    report_id: uuid.UUID,
    identity: ReportRequestIdentity | None,
) -> AITaskRun | None:
    if identity is None:
        return None
    run = db.scalar(
        select(AITaskRun).where(
            AITaskRun.actor_user_id == user_id,
            AITaskRun.request_idempotency_key_hash == identity.key_hash,
        )
    )
    if run is None:
        return None
    if run.report_id != report_id:
        raise ReportIdempotencyConflictError(
            "The Idempotency-Key is already associated with another report retry."
        )
    _ensure_matching_fingerprint(run.request_fingerprint, identity.fingerprint)
    return _canonical_report_task_run(db, run=run)


def find_report_schedule_run_replay(
    db: Session,
    *,
    user_id: uuid.UUID,
    schedule_id: uuid.UUID,
    identity: ReportRequestIdentity | None,
) -> tuple[Report, AITaskRun | None] | None:
    if identity is None:
        return None
    report = db.scalar(
        select(Report).where(
            Report.schedule_id == schedule_id,
            Report.request_idempotency_key_hash == identity.key_hash,
        )
    )
    if report is None:
        return None
    _ensure_matching_fingerprint(report.request_fingerprint, identity.fingerprint)
    run = _request_report_task_run(db, report=report)
    if run is None and report.status == "skipped":
        return report, None
    if run is None:
        raise ReportIdempotencyConflictError(
            "The original schedule run exists, but its task record is unavailable. "
            "Use a new Idempotency-Key to run the schedule again."
        )
    if run.actor_user_id != user_id:
        return None
    return report, run


def _request_report_task_run(db: Session, *, report: Report) -> AITaskRun | None:
    try:
        return find_report_request_task_run(db, report=report)
    except ReportTaskLineageError as exc:
        raise ReportIdempotencyConflictError(
            "The original report task has invalid supersession history. "
            "Contact an administrator before retrying this request."
        ) from exc


def _canonical_report_task_run(db: Session, *, run: AITaskRun) -> AITaskRun:
    try:
        return resolve_report_task_run(db, run)
    except ReportTaskLineageError as exc:
        raise ReportIdempotencyConflictError(
            "The original report task has invalid supersession history. "
            "Contact an administrator before retrying this request."
        ) from exc


def _normalize_key(key: str | None) -> str | None:
    if key is None:
        return None
    normalized = key.strip()
    if not normalized:
        raise ReportIdempotencyError("Idempotency-Key must not be blank.")
    if len(normalized) > MAX_IDEMPOTENCY_KEY_LENGTH:
        raise ReportIdempotencyError(
            f"Idempotency-Key must be at most {MAX_IDEMPOTENCY_KEY_LENGTH} characters."
        )
    return normalized


def _ensure_matching_fingerprint(
    stored: str | None,
    requested: str,
    *,
    conflict_message: str = (
        "The Idempotency-Key was already used with a different report request."
    ),
) -> None:
    if stored != requested:
        raise ReportIdempotencyConflictError(conflict_message)


def _payload_fingerprint(payload: object) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return _sha256(canonical)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


__all__ = [
    "ReportIdempotencyConflictError",
    "ReportIdempotencyError",
    "ReportRequestIdentity",
    "build_report_create_identity",
    "build_report_operation_identity",
    "build_report_retry_identity",
    "build_report_schedule_run_identity",
    "find_report_create_replay",
    "find_report_operation_replay",
    "find_report_retry_replay",
    "find_report_schedule_run_replay",
    "record_report_operation_receipt",
]
