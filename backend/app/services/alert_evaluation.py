from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.alert_evaluation_request import (
    AlertEvaluationRequest,
    AlertEvaluationRequestActivity,
)
from app.models.alert_backfill_preview import AlertBackfillPreview
from app.models.feed import Feed
from app.models.item import Item
from app.services.alert_acceptance import (
    AlertEvaluationIntent,
    lock_alert_evaluation_item,
    persist_alert_evaluation_intent,
    reset_alert_evaluation_for_backfill,
)
from app.services.alert_backfill_receipts import (
    ALERT_BACKFILL_APPLY_RESULT_KEY as _ALERT_BACKFILL_APPLY_RESULT_KEY,
    ALERT_BACKFILL_APPLY_RESULT_VERSION as _ALERT_BACKFILL_APPLY_RESULT_VERSION,
    AlertBackfillCandidate,
    AlertBackfillPersistenceResult,
    AlertBackfillPreviewError,
    alert_backfill_preview_fingerprint as _alert_backfill_preview_fingerprint,
    alert_backfill_response_payload as _alert_backfill_response_payload,
    load_alert_backfill_apply_result as _load_alert_backfill_apply_result,
    parse_alert_backfill_candidates as _parse_alert_backfill_candidates,
)
from app.services.alert_evaluation_execution import (
    AlertEvaluationExecutionError as AlertEvaluationError,
    AlertEvaluationExecutionLeaseLost as AlertEvaluationLeaseLost,
    AlertEvaluationOutcome,
    evaluate_alert_request,
)
from app.services.alert_evaluation_history import record_alert_evaluation_activity
from app.services.data_access_policy import (
    DataAccessContext,
    handling_label_access_predicate,
)


ALERT_EVALUATION_MAX_ATTEMPTS = 5
ALERT_EVALUATION_LEASE_SECONDS = 120
ALERT_EVALUATION_DISPATCH_STALE_SECONDS = 300
ALERT_EVALUATION_REPUBLISH_BASE_SECONDS = 300
ALERT_EVALUATION_REPUBLISH_MAX_SECONDS = 6 * 60 * 60
ALERT_EVALUATION_RETRY_BASE_SECONDS = 30
ALERT_EVALUATION_RETRY_MAX_SECONDS = 3_600
ALERT_EVALUATION_RETRY_JITTER_RATIO = 0.20
ALERT_EVALUATION_RECONCILE_BATCH_SIZE = 100
ALERT_BACKFILL_PREVIEW_TTL_SECONDS = 15 * 60
_ALERT_BACKFILL_RECEIPT_TTL_SECONDS = 24 * 60 * 60

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
    "record_alert_evaluation_publications",
    "record_direct_alert_evaluation_publications",
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
class AlertBackfillCandidatePage:
    candidates: tuple[AlertBackfillCandidate, ...]
    matched_count: int
    truncated: bool
    next_cursor_first_seen_at: datetime | None
    next_cursor_item_id: uuid.UUID | None


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
    request.dispatch_published_at = None
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
    request.dispatch_published_at = None
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


def alert_evaluation_republish_delay(dispatch_attempt_count: int) -> int:
    attempt = max(1, int(dispatch_attempt_count or 1))
    return min(
        ALERT_EVALUATION_REPUBLISH_MAX_SECONDS,
        ALERT_EVALUATION_REPUBLISH_BASE_SECONDS * (2 ** min(attempt - 1, 16)),
    )


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
    publication_due = _alert_evaluation_publication_due(current_time)
    due_dispatch = and_(
        AlertEvaluationRequest.state.in_(["pending", "retry_wait"]),
        AlertEvaluationRequest.available_at <= current_time,
        publication_due,
    )
    stale_processing = and_(
        AlertEvaluationRequest.state == "processing",
        or_(
            AlertEvaluationRequest.lease_expires_at.is_(None),
            AlertEvaluationRequest.lease_expires_at <= current_time,
        ),
        publication_due,
    )
    rows = list(
        db.scalars(
            select(AlertEvaluationRequest)
            .where(dispatch_available, or_(due_dispatch, stale_processing))
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
        request.dispatch_published_at = None
        request.dispatch_attempt_count = (
            max(0, int(request.dispatch_attempt_count or 0)) + 1
        )
        db.add(request)
    db.flush()
    return AlertEvaluationReservation(
        tuple(request.id for request in rows), current_time
    )


def record_alert_evaluation_publications(
    db: Session,
    *,
    request_ids: list[uuid.UUID],
    reserved_at: datetime,
    published_at: datetime | None = None,
) -> None:
    _record_alert_evaluation_publications(
        db,
        request_ids=request_ids,
        expected_claim=lambda _request: reserved_at,
        published_at=published_at,
    )


def record_direct_alert_evaluation_publications(
    db: Session,
    *,
    request_ids: list[uuid.UUID],
    published_at: datetime | None = None,
) -> None:
    _record_alert_evaluation_publications(
        db,
        request_ids=request_ids,
        expected_claim=_direct_dispatch_claim_at,
        published_at=published_at,
    )


def _record_alert_evaluation_publications(
    db: Session,
    *,
    request_ids: list[uuid.UUID],
    expected_claim,
    published_at: datetime | None,
) -> None:
    if not request_ids:
        return
    current_time = _as_utc(published_at or datetime.now(timezone.utc))
    rows = db.scalars(
        select(AlertEvaluationRequest)
        .where(
            AlertEvaluationRequest.id.in_(request_ids),
            AlertEvaluationRequest.state.in_(["pending", "retry_wait", "processing"]),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    for request in rows:
        if _same_instant(request.dispatch_claimed_at, expected_claim(request)):
            request.dispatch_published_at = current_time
            db.add(request)


def _alert_evaluation_publication_due(current_time: datetime):
    clauses = [AlertEvaluationRequest.dispatch_published_at.is_(None)]
    attempt = 1
    while (
        alert_evaluation_republish_delay(attempt)
        < ALERT_EVALUATION_REPUBLISH_MAX_SECONDS
    ):
        attempt_predicate = (
            AlertEvaluationRequest.dispatch_attempt_count <= 1
            if attempt == 1
            else AlertEvaluationRequest.dispatch_attempt_count == attempt
        )
        clauses.append(
            and_(
                attempt_predicate,
                AlertEvaluationRequest.dispatch_published_at
                <= current_time
                - timedelta(seconds=alert_evaluation_republish_delay(attempt)),
            )
        )
        attempt += 1
    clauses.append(
        and_(
            AlertEvaluationRequest.dispatch_attempt_count >= attempt,
            AlertEvaluationRequest.dispatch_published_at
            <= current_time - timedelta(seconds=ALERT_EVALUATION_REPUBLISH_MAX_SECONDS),
        )
    )
    return or_(*clauses)


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
        direct_claim_at = _direct_dispatch_claim_at(request)
        if _same_instant(request.dispatch_claimed_at, direct_claim_at):
            _record_dispatch_failure(request, now=datetime.now(timezone.utc))
            _record_dispatch_failure_activity(db, request)


def _record_dispatch_failure(
    request: AlertEvaluationRequest,
    *,
    now: datetime,
) -> None:
    request.dispatch_claimed_at = None
    request.dispatch_published_at = None
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
    data_access: DataAccessContext,
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
    rows = db.execute(
        select(Item, func.count(Item.id).over().label("matched_count"))
        .join(Feed, Feed.id == Item.feed_id)
        .where(
            *predicates,
            handling_label_access_predicate(Feed.handling_label_id, data_access),
        )
        .order_by(Item.first_seen_at.asc(), Item.id.asc())
        .limit(bounded_limit + 1)
    ).all()
    matched_count = int(rows[0].matched_count) if rows else 0
    truncated = len(rows) > bounded_limit
    candidates = tuple(
        AlertBackfillCandidate(
            item.id, item.content_hash, item.title[:512], item.first_seen_at
        )
        for item, _matched_count in rows[:bounded_limit]
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
    data_access: DataAccessContext,
    since: datetime,
    until: datetime,
    limit: int,
    actor_user_id: uuid.UUID | None = None,
    cursor_first_seen_at: datetime | None = None,
    cursor_item_id: uuid.UUID | None = None,
) -> AlertBackfillPersistenceResult:
    page = list_alert_backfill_candidates(
        db,
        data_access=data_access,
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
        item = _get_accessible_backfill_item_for_update(
            db,
            item_id=candidate.item_id,
            data_access=data_access,
        )
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
    data_access: DataAccessContext,
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
        data_access=data_access,
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
    data_access: DataAccessContext,
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
        replayed_result = _load_alert_backfill_apply_result(db, preview)
        if replayed_result is not None:
            return replayed_result
        raise AlertBackfillPreviewError(
            "This alert backfill preview was already applied by an earlier ThreatLens version and its exact result cannot be replayed. Preview the next page or start a new backfill.",
            code="alert_backfill_preview_consumed",
        )
    if _as_utc(preview.expires_at) <= current_time:
        raise AlertBackfillPreviewError(
            "This alert backfill preview expired. Recalculate it before applying.",
            code="alert_backfill_preview_expired",
        )

    candidates = _parse_alert_backfill_candidates(preview)
    candidate_entries = list(preview.candidates_json)
    outcomes: list[dict[str, object]] = []
    request_ids: list[uuid.UUID] = []
    existing_count = 0
    skipped_count = 0
    for candidate in candidates:
        outcome: dict[str, object] = {
            "item_id": str(candidate.item_id),
            "content_hash": candidate.content_hash,
        }
        item = _get_accessible_backfill_item_for_update(
            db,
            item_id=candidate.item_id,
            data_access=data_access,
        )
        if item is None or item.content_hash != candidate.content_hash:
            outcome["status"] = "skipped"
            skipped_count += 1
            outcomes.append(outcome)
            continue
        intent = _persist_explicit_backfill_intent(
            db,
            item=item,
            actor_user_id=actor_user_id,
            now=current_time,
        )
        if intent is None:
            outcome["status"] = "skipped"
            skipped_count += 1
        elif not intent.created:
            outcome["status"] = "existing"
            existing_count += 1
        else:
            request = db.get(AlertEvaluationRequest, intent.request_id)
            if request is None or intent.activity_id is None:
                raise RuntimeError(
                    "Persisted alert backfill generation could not be loaded."
                )
            activity = db.get(AlertEvaluationRequestActivity, intent.activity_id)
            if (
                activity is None
                or activity.request_id != request.id
                or activity.actor_user_id != actor_user_id
                or activity.action not in {"accepted", "backfill_requested"}
            ):
                raise RuntimeError(
                    "Persisted alert backfill activity could not be verified."
                )
            activity.details_json = {
                **(
                    activity.details_json
                    if isinstance(activity.details_json, dict)
                    else {}
                ),
                "backfill_preview_id": str(preview.id),
            }
            db.add(activity)
            _prepare_backfill_request_for_durable_dispatch(request)
            request_ids.append(request.id)
            outcome.update(
                {
                    "status": "accepted",
                    "request_id": str(request.id),
                    "request_version": request.version,
                    "backfill_count": request.backfill_count,
                    "accepted_at": _as_utc(request.accepted_at).isoformat(),
                    "activity_id": str(activity.id),
                }
            )
        outcomes.append(outcome)

    result = AlertBackfillPersistenceResult(
        tuple(request_ids),
        existing_count,
        skipped_count,
        preview.next_cursor_first_seen_at,
        preview.next_cursor_item_id,
    )
    preview.candidates_json = [
        *candidate_entries,
        {
            _ALERT_BACKFILL_APPLY_RESULT_KEY: {
                "version": _ALERT_BACKFILL_APPLY_RESULT_VERSION,
                "preview_fingerprint": _alert_backfill_preview_fingerprint(
                    preview, candidates
                ),
                "outcomes": outcomes,
                "response": _alert_backfill_response_payload(preview, result),
            }
        },
    ]
    preview.consumed_at = current_time
    preview.expires_at = max(
        _as_utc(preview.expires_at),
        current_time + timedelta(seconds=_ALERT_BACKFILL_RECEIPT_TTL_SECONDS),
    )
    db.add(preview)
    db.flush()
    return result


def _prepare_backfill_request_for_durable_dispatch(
    request: AlertEvaluationRequest,
) -> None:
    if (
        request.active_source != "backfill"
        or request.notify is not False
        or request.notify_existing_occurrences is not False
        or request.respect_rule_cutover is not False
        or request.state != "pending"
    ):
        raise RuntimeError("Alert backfill request was persisted with unsafe flags.")
    request.dispatch_claimed_at = None
    request.dispatch_published_at = None
    request.dispatch_attempt_count = 0


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
            actor_user_id=actor_user_id,
            now=now,
        )
    return reset_alert_evaluation_for_backfill(
        db,
        request=request,
        item=item,
        actor_user_id=actor_user_id,
        now=now,
    )


def _get_accessible_backfill_item_for_update(
    db: Session,
    *,
    item_id: uuid.UUID,
    data_access: DataAccessContext,
) -> Item | None:
    return db.scalar(
        select(Item)
        .join(Feed, Feed.id == Item.feed_id)
        .where(
            Item.id == item_id,
            handling_label_access_predicate(
                Feed.handling_label_id,
                data_access,
            ),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _is_after(value: datetime | None, reference: datetime) -> bool:
    return value is not None and _as_utc(value) > _as_utc(reference)


def _same_instant(left: datetime | None, right: datetime) -> bool:
    return left is not None and _as_utc(left) == _as_utc(right)


def _direct_dispatch_claim_at(request: AlertEvaluationRequest) -> datetime:
    return (
        request.last_replayed_at
        if request.active_source == "replay" and request.last_replayed_at is not None
        else request.accepted_at
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
