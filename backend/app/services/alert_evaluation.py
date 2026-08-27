from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.alert_evaluation_request import AlertEvaluationRequest
from app.models.alert_backfill_preview import AlertBackfillPreview
from app.models.item import Item
from app.services.alert_acceptance import (
    AlertEvaluationIntent,
    lock_alert_evaluation_item,
    persist_alert_evaluation_intent,
    reset_alert_evaluation_for_backfill,
)
from app.services.alert_evaluation_execution import (
    AlertEvaluationExecutionError as AlertEvaluationError,
    AlertEvaluationExecutionLeaseLost as AlertEvaluationLeaseLost,
    AlertEvaluationOutcome,
    evaluate_alert_request,
)
from app.services.alert_evaluation_history import record_alert_evaluation_activity


ALERT_EVALUATION_MAX_ATTEMPTS = 5
ALERT_EVALUATION_LEASE_SECONDS = 120
ALERT_EVALUATION_DISPATCH_STALE_SECONDS = 300
ALERT_EVALUATION_RETRY_BASE_SECONDS = 30
ALERT_EVALUATION_RETRY_MAX_SECONDS = 3_600
ALERT_EVALUATION_RETRY_JITTER_RATIO = 0.20
ALERT_EVALUATION_RECONCILE_BATCH_SIZE = 100
ALERT_BACKFILL_PREVIEW_TTL_SECONDS = 15 * 60

__all__ = [
    "AlertBackfillPreviewError",
    "AlertEvaluationError",
    "AlertEvaluationIntent",
    "AlertEvaluationLeaseLost",
    "AlertEvaluationOutcome",
    "claim_alert_evaluation_request",
    "create_alert_backfill_preview",
    "evaluate_alert_request",
    "list_alert_backfill_candidates",
    "persist_alert_backfill_intents",
    "persist_alert_backfill_preview_intents",
    "persist_alert_evaluation_intent",
    "record_alert_evaluation_failure",
    "alert_evaluation_retry_delay",
    "release_alert_evaluation_publications",
    "release_failed_direct_alert_publications",
    "reserve_recoverable_alert_evaluations",
    "safe_alert_evaluation_error",
]


@dataclass(frozen=True)
class AlertEvaluationClaim:
    request_id: uuid.UUID
    lease_token: str
    attempt_number: int


@dataclass(frozen=True)
class AlertEvaluationFailureOutcome:
    request_id: uuid.UUID
    state: str
    error_code: str
    retry_at: datetime | None


@dataclass(frozen=True)
class AlertEvaluationReservation:
    request_ids: tuple[uuid.UUID, ...]
    reserved_at: datetime


@dataclass(frozen=True)
class AlertBackfillCandidate:
    item_id: uuid.UUID
    content_hash: str
    title: str
    first_seen_at: datetime


@dataclass(frozen=True)
class AlertBackfillCandidatePage:
    candidates: tuple[AlertBackfillCandidate, ...]
    matched_count: int
    truncated: bool
    next_cursor_first_seen_at: datetime | None
    next_cursor_item_id: uuid.UUID | None


@dataclass(frozen=True)
class AlertBackfillPersistenceResult:
    request_ids: tuple[uuid.UUID, ...]
    existing_count: int
    skipped_count: int
    next_cursor_first_seen_at: datetime | None
    next_cursor_item_id: uuid.UUID | None


class AlertBackfillPreviewError(RuntimeError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class AlertBackfillPreviewSnapshot:
    preview: AlertBackfillPreview
    candidates: tuple[AlertBackfillCandidate, ...]


def claim_alert_evaluation_request(
    db: Session,
    *,
    request_id: uuid.UUID,
    now: datetime | None = None,
) -> AlertEvaluationClaim | None:
    current_time = now or datetime.now(timezone.utc)
    request = db.scalar(
        select(AlertEvaluationRequest)
        .where(AlertEvaluationRequest.id == request_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if request is None or request.state in {"succeeded", "dead_letter"}:
        return None
    if request.state == "processing" and _is_after(
        request.lease_expires_at, current_time
    ):
        return None
    if request.state in {"pending", "retry_wait"} and _is_after(
        request.available_at, current_time
    ):
        return None
    if int(request.attempt_count or 0) >= int(
        request.max_attempts or ALERT_EVALUATION_MAX_ATTEMPTS
    ):
        request.state = "dead_letter"
        request.completed_at = current_time
        request.dispatch_claimed_at = None
        request.claimed_at = None
        request.lease_token = None
        request.lease_expires_at = None
        request.last_error_code = "attempt_limit_exceeded"
        request.last_error_message = "Alert evaluation exhausted its retry budget."
        request.version = max(1, int(request.version or 1)) + 1
        db.add(request)
        record_alert_evaluation_activity(
            db,
            request_id=request.id,
            action="dead_lettered",
            details={"error_code": "attempt_limit_exceeded"},
        )
        return None

    token = secrets.token_hex(16)
    request.state = "processing"
    request.attempt_count = max(0, int(request.attempt_count or 0)) + 1
    request.claimed_at = current_time
    request.lease_token = token
    request.lease_expires_at = current_time + timedelta(
        seconds=ALERT_EVALUATION_LEASE_SECONDS
    )
    request.dispatch_claimed_at = None
    request.last_error_code = None
    request.last_error_message = None
    request.version = max(1, int(request.version or 1)) + 1
    db.add(request)
    db.flush()
    return AlertEvaluationClaim(request.id, token, request.attempt_count)


def record_alert_evaluation_failure(
    db: Session,
    *,
    request_id: uuid.UUID,
    lease_token: str,
    error: Exception,
    now: datetime | None = None,
) -> AlertEvaluationFailureOutcome | None:
    current_time = now or datetime.now(timezone.utc)
    request = db.scalar(
        select(AlertEvaluationRequest)
        .where(AlertEvaluationRequest.id == request_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        request is None
        or request.state != "processing"
        or request.lease_token != lease_token
    ):
        return None

    code, message, retryable = safe_alert_evaluation_error(error)
    terminal = not retryable or int(request.attempt_count or 0) >= int(
        request.max_attempts or 1
    )
    retry_at = None
    if terminal:
        request.state = "dead_letter"
        request.completed_at = current_time
    else:
        delay = alert_evaluation_retry_delay(
            int(request.attempt_count or 1),
            jitter_fraction=secrets.randbelow(10_001) / 10_000,
        )
        retry_at = current_time + timedelta(seconds=delay)
        request.state = "retry_wait"
        request.available_at = retry_at
        request.completed_at = None
    request.claimed_at = None
    request.lease_token = None
    request.lease_expires_at = None
    request.dispatch_claimed_at = None
    request.last_error_code = code
    request.last_error_message = message
    request.version = max(1, int(request.version or 1)) + 1
    db.add(request)
    record_alert_evaluation_activity(
        db,
        request_id=request.id,
        action="dead_lettered" if terminal else "retry_scheduled",
        details={
            "error_code": code,
            "attempt_count": max(0, int(request.attempt_count or 0)),
            **({"retry_at": retry_at.isoformat()} if retry_at is not None else {}),
        },
    )
    db.flush()
    return AlertEvaluationFailureOutcome(request.id, request.state, code, retry_at)


def safe_alert_evaluation_error(error: Exception) -> tuple[str, str, bool]:
    if isinstance(error, AlertEvaluationError):
        return error.code, error.public_message, error.retryable
    if isinstance(error, SQLAlchemyError):
        return (
            "evaluation_database_error",
            "Alert evaluation could not access required persisted data and will retry automatically.",
            True,
        )
    return (
        "evaluation_worker_error",
        "Alert evaluation failed unexpectedly and will retry automatically.",
        True,
    )


def alert_evaluation_retry_delay(
    attempt_count: int,
    *,
    jitter_fraction: float,
) -> float:
    """Return bounded exponential backoff with deterministic full-range jitter input."""
    nominal = min(
        ALERT_EVALUATION_RETRY_MAX_SECONDS,
        ALERT_EVALUATION_RETRY_BASE_SECONDS * (2 ** max(0, int(attempt_count) - 1)),
    )
    lower = nominal * (1 - ALERT_EVALUATION_RETRY_JITTER_RATIO)
    upper = min(
        float(ALERT_EVALUATION_RETRY_MAX_SECONDS),
        nominal * (1 + ALERT_EVALUATION_RETRY_JITTER_RATIO),
    )
    fraction = max(0.0, min(float(jitter_fraction), 1.0))
    return lower + ((upper - lower) * fraction)


def reserve_recoverable_alert_evaluations(
    db: Session,
    *,
    now: datetime | None = None,
    batch_size: int = ALERT_EVALUATION_RECONCILE_BATCH_SIZE,
) -> AlertEvaluationReservation:
    current_time = now or datetime.now(timezone.utc)
    stale_dispatch = current_time - timedelta(
        seconds=ALERT_EVALUATION_DISPATCH_STALE_SECONDS
    )
    dispatch_available = or_(
        AlertEvaluationRequest.dispatch_claimed_at.is_(None),
        AlertEvaluationRequest.dispatch_claimed_at < stale_dispatch,
    )
    due_unclaimed = and_(
        AlertEvaluationRequest.state.in_(["pending", "retry_wait"]),
        AlertEvaluationRequest.available_at <= current_time,
    )
    stale_processing = and_(
        AlertEvaluationRequest.state == "processing",
        or_(
            AlertEvaluationRequest.lease_expires_at.is_(None),
            AlertEvaluationRequest.lease_expires_at <= current_time,
        ),
    )
    rows = list(
        db.scalars(
            select(AlertEvaluationRequest)
            .where(dispatch_available, or_(due_unclaimed, stale_processing))
            .order_by(
                AlertEvaluationRequest.available_at.asc(),
                AlertEvaluationRequest.created_at.asc(),
            )
            .limit(max(1, min(int(batch_size), 500)))
            .with_for_update(skip_locked=True)
        ).all()
    )
    for request in rows:
        request.dispatch_claimed_at = current_time
        request.dispatch_attempt_count = (
            max(0, int(request.dispatch_attempt_count or 0)) + 1
        )
        db.add(request)
    db.flush()
    return AlertEvaluationReservation(
        tuple(request.id for request in rows), current_time
    )


def release_alert_evaluation_publications(
    db: Session,
    *,
    request_ids: list[uuid.UUID],
    reserved_at: datetime,
) -> None:
    if not request_ids:
        return
    rows = db.scalars(
        select(AlertEvaluationRequest)
        .where(AlertEvaluationRequest.id.in_(request_ids))
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    for request in rows:
        if _same_instant(request.dispatch_claimed_at, reserved_at):
            _record_dispatch_failure(request, now=datetime.now(timezone.utc))
            _record_dispatch_failure_activity(db, request)


def release_failed_direct_alert_publications(
    db: Session,
    *,
    request_ids: list[uuid.UUID],
) -> None:
    if not request_ids:
        return
    rows = db.scalars(
        select(AlertEvaluationRequest)
        .where(
            AlertEvaluationRequest.id.in_(request_ids),
            AlertEvaluationRequest.state.in_(["pending", "retry_wait"]),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    for request in rows:
        direct_claim_at = (
            request.last_replayed_at
            if request.active_source == "replay"
            else request.accepted_at
        )
        if _same_instant(request.dispatch_claimed_at, direct_claim_at):
            _record_dispatch_failure(request, now=datetime.now(timezone.utc))
            _record_dispatch_failure_activity(db, request)


def _record_dispatch_failure(
    request: AlertEvaluationRequest,
    *,
    now: datetime,
) -> None:
    request.dispatch_claimed_at = None
    request.dispatch_failure_count = (
        max(0, int(request.dispatch_failure_count or 0)) + 1
    )
    request.last_dispatch_failed_at = now
    request.last_error_code = "evaluation_dispatch_failed"
    request.last_error_message = (
        "The evaluation is durable, but it could not be published to the worker queue. "
        "Background reconciliation will retry automatically."
    )
    request.version = max(1, int(request.version or 1)) + 1


def _record_dispatch_failure_activity(
    db: Session,
    request: AlertEvaluationRequest,
) -> None:
    db.add(request)
    failure_count = max(1, int(request.dispatch_failure_count or 1))
    if failure_count & (failure_count - 1) == 0:
        record_alert_evaluation_activity(
            db,
            request_id=request.id,
            action="dispatch_failed",
            details={"dispatch_failure_count": failure_count},
        )


def list_alert_backfill_candidates(
    db: Session,
    *,
    since: datetime,
    until: datetime,
    limit: int,
    cursor_first_seen_at: datetime | None = None,
    cursor_item_id: uuid.UUID | None = None,
) -> AlertBackfillCandidatePage:
    bounded_limit = max(1, min(int(limit), 500))
    predicates: list = [
        Item.first_seen_at >= since,
        Item.first_seen_at <= until,
    ]
    if cursor_first_seen_at is not None and cursor_item_id is not None:
        predicates.append(
            or_(
                Item.first_seen_at > cursor_first_seen_at,
                and_(
                    Item.first_seen_at == cursor_first_seen_at,
                    Item.id > cursor_item_id,
                ),
            )
        )
    matched_count = db.scalar(select(func.count(Item.id)).where(*predicates)) or 0
    rows = db.scalars(
        select(Item)
        .where(*predicates)
        .order_by(Item.first_seen_at.asc(), Item.id.asc())
        .limit(bounded_limit + 1)
    ).all()
    truncated = len(rows) > bounded_limit
    candidates = tuple(
        AlertBackfillCandidate(
            item.id, item.content_hash, item.title[:512], item.first_seen_at
        )
        for item in rows[:bounded_limit]
    )
    next_candidate = candidates[-1] if truncated and candidates else None
    return AlertBackfillCandidatePage(
        candidates,
        int(matched_count),
        truncated,
        next_candidate.first_seen_at if next_candidate is not None else None,
        next_candidate.item_id if next_candidate is not None else None,
    )


def persist_alert_backfill_intents(
    db: Session,
    *,
    since: datetime,
    until: datetime,
    limit: int,
    actor_user_id: uuid.UUID | None = None,
    cursor_first_seen_at: datetime | None = None,
    cursor_item_id: uuid.UUID | None = None,
) -> AlertBackfillPersistenceResult:
    page = list_alert_backfill_candidates(
        db,
        since=since,
        until=until,
        limit=limit,
        cursor_first_seen_at=cursor_first_seen_at,
        cursor_item_id=cursor_item_id,
    )
    request_ids: list[uuid.UUID] = []
    existing_count = 0
    skipped_count = 0
    for candidate in page.candidates:
        item = db.get(Item, candidate.item_id)
        if item is None or item.content_hash != candidate.content_hash:
            skipped_count += 1
            continue
        intent = _persist_explicit_backfill_intent(
            db,
            item=item,
            actor_user_id=actor_user_id,
        )
        if intent is None:
            skipped_count += 1
        elif intent.created:
            request_ids.append(intent.request_id)
        else:
            existing_count += 1
    return AlertBackfillPersistenceResult(
        tuple(request_ids),
        existing_count,
        skipped_count,
        page.next_cursor_first_seen_at,
        page.next_cursor_item_id,
    )


def create_alert_backfill_preview(
    db: Session,
    *,
    actor_user_id: uuid.UUID,
    since: datetime,
    until: datetime,
    limit: int,
    cursor_first_seen_at: datetime | None = None,
    cursor_item_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> AlertBackfillPreviewSnapshot:
    current_time = _as_utc(now or datetime.now(timezone.utc))
    page = list_alert_backfill_candidates(
        db,
        since=since,
        until=until,
        limit=limit,
        cursor_first_seen_at=cursor_first_seen_at,
        cursor_item_id=cursor_item_id,
    )
    preview = AlertBackfillPreview(
        actor_user_id=actor_user_id,
        since=since,
        until=until,
        item_limit=max(1, min(int(limit), 500)),
        cursor_first_seen_at=cursor_first_seen_at,
        cursor_item_id=cursor_item_id,
        candidates_json=[
            {
                "item_id": str(candidate.item_id),
                "content_hash": candidate.content_hash,
                "title": candidate.title,
                "first_seen_at": _as_utc(candidate.first_seen_at).isoformat(),
            }
            for candidate in page.candidates
        ],
        matched_count=page.matched_count,
        has_more=page.truncated,
        next_cursor_first_seen_at=page.next_cursor_first_seen_at,
        next_cursor_item_id=page.next_cursor_item_id,
        expires_at=current_time + timedelta(seconds=ALERT_BACKFILL_PREVIEW_TTL_SECONDS),
    )
    db.add(preview)
    db.flush()
    return AlertBackfillPreviewSnapshot(preview, page.candidates)


def persist_alert_backfill_preview_intents(
    db: Session,
    *,
    preview_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    now: datetime | None = None,
) -> AlertBackfillPersistenceResult:
    current_time = _as_utc(now or datetime.now(timezone.utc))
    preview = db.scalar(
        select(AlertBackfillPreview)
        .where(
            AlertBackfillPreview.id == preview_id,
            AlertBackfillPreview.actor_user_id == actor_user_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if preview is None:
        raise AlertBackfillPreviewError(
            "The alert backfill preview was not found for this administrator.",
            code="alert_backfill_preview_not_found",
        )
    if preview.consumed_at is not None:
        raise AlertBackfillPreviewError(
            "This alert backfill preview was already applied. Preview the next page or start a new backfill.",
            code="alert_backfill_preview_consumed",
        )
    if _as_utc(preview.expires_at) <= current_time:
        raise AlertBackfillPreviewError(
            "This alert backfill preview expired. Recalculate it before applying.",
            code="alert_backfill_preview_expired",
        )

    request_ids: list[uuid.UUID] = []
    existing_count = 0
    skipped_count = 0
    for candidate in preview.candidates_json or []:
        try:
            item_id = uuid.UUID(str(candidate["item_id"]))
            expected_hash = str(candidate["content_hash"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AlertBackfillPreviewError(
                "The persisted alert backfill preview is invalid. Recalculate it before applying.",
                code="alert_backfill_preview_invalid",
            ) from exc
        item = db.scalar(select(Item).where(Item.id == item_id).with_for_update())
        if item is None or item.content_hash != expected_hash:
            skipped_count += 1
            continue
        intent = _persist_explicit_backfill_intent(
            db,
            item=item,
            actor_user_id=actor_user_id,
            now=current_time,
        )
        if intent is None:
            skipped_count += 1
        elif intent.created:
            request_ids.append(intent.request_id)
        else:
            existing_count += 1

    preview.consumed_at = current_time
    db.add(preview)
    db.flush()
    return AlertBackfillPersistenceResult(
        tuple(request_ids),
        existing_count,
        skipped_count,
        preview.next_cursor_first_seen_at,
        preview.next_cursor_item_id,
    )


def _persist_explicit_backfill_intent(
    db: Session,
    *,
    item: Item,
    actor_user_id: uuid.UUID | None,
    now: datetime | None = None,
) -> AlertEvaluationIntent | None:
    locked_item = lock_alert_evaluation_item(db, item_id=item.id)
    if locked_item is None:
        return None
    item = locked_item
    request = db.scalar(
        select(AlertEvaluationRequest)
        .where(
            AlertEvaluationRequest.item_id == item.id,
            AlertEvaluationRequest.item_content_hash == item.content_hash,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if request is None:
        return persist_alert_evaluation_intent(
            db,
            item=item,
            source="backfill",
            notify=False,
            respect_rule_cutover=False,
            now=now,
        )
    return reset_alert_evaluation_for_backfill(
        db,
        request=request,
        item=item,
        actor_user_id=actor_user_id,
        now=now,
    )


def _is_after(value: datetime | None, reference: datetime) -> bool:
    return value is not None and _as_utc(value) > _as_utc(reference)


def _same_instant(left: datetime | None, right: datetime) -> bool:
    return left is not None and _as_utc(left) == _as_utc(right)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
