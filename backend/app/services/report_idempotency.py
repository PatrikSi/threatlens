from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai_task_run import AITaskRun
from app.models.report import Report


MAX_IDEMPOTENCY_KEY_LENGTH = 255


class ReportIdempotencyError(ValueError):
    pass


class ReportIdempotencyConflictError(ReportIdempotencyError):
    pass


@dataclass(frozen=True)
class ReportRequestIdentity:
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
        key_hash=_sha256(f"{scope}\0{normalized}"),
        fingerprint=_payload_fingerprint(
            {"operation": "retry", "report_id": str(report_id), "version": 1}
        ),
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
            Report.request_idempotency_key_hash == identity.key_hash,
        )
    )
    if report is None:
        return None
    _ensure_matching_fingerprint(report.request_fingerprint, identity.fingerprint)
    run = db.scalar(
        select(AITaskRun)
        .where(
            AITaskRun.report_id == report.id,
            AITaskRun.task_type == "report",
        )
        .order_by(AITaskRun.created_at.asc(), AITaskRun.id.asc())
        .limit(1)
    )
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
    return run


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


def _ensure_matching_fingerprint(stored: str | None, requested: str) -> None:
    if stored != requested:
        raise ReportIdempotencyConflictError(
            "The Idempotency-Key was already used with a different report request."
        )


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
    "build_report_retry_identity",
    "find_report_create_replay",
    "find_report_retry_replay",
]
