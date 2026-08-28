from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.models.alert_evaluation_request import (
    AlertEvaluationRequest,
    AlertEvaluationRequestActivity,
)
from app.models.alert_occurrence import AlertOccurrence, AlertOccurrenceMetric
from app.services.alert_acceptance import lock_alert_evaluation_item_and_request
from app.services.alert_evaluation_history import record_alert_evaluation_activity


ALERT_EVALUATION_STATES = frozenset(
    {"pending", "processing", "retry_wait", "succeeded", "dead_letter"}
)
ALERT_EVALUATION_SOURCES = frozenset({"live", "reconciliation", "backfill", "replay"})


class AlertEvaluationNotFoundError(LookupError):
    code = "alert_evaluation_not_found"


class AlertEvaluationConflictError(RuntimeError):
    def __init__(self, message: str, *, code: str, current_version: int) -> None:
        super().__init__(message)
        self.code = code
        self.current_version = current_version


class AlertEvaluationValidationError(ValueError):
    code = "alert_evaluation_invalid"


@dataclass(frozen=True)
class AlertEvaluationRequestPage:
    items: list[AlertEvaluationRequest]
    total: int
    page: int
    page_size: int


@dataclass(frozen=True)
class AlertEvaluationActivityPage:
    items: list[AlertEvaluationRequestActivity]
    total: int
    page: int
    page_size: int


@dataclass(frozen=True)
class AlertOccurrenceMetricPoint:
    id: uuid.UUID
    bucket_start: datetime
    owner_user_id: uuid.UUID
    severity: str
    lifecycle_state: str
    suppressed: bool
    occurrence_count: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class AlertOccurrenceMetricPage:
    items: list[AlertOccurrenceMetricPoint]
    truncated: bool


def list_alert_evaluation_requests(
    db: Session,
    *,
    states: list[str],
    sources: list[str],
    item_id: uuid.UUID | None,
    page: int,
    page_size: int,
    needs_attention: bool = False,
    now: datetime | None = None,
) -> AlertEvaluationRequestPage:
    current_time = _as_utc(now or datetime.now(timezone.utc))
    predicates = []
    if states:
        predicates.append(AlertEvaluationRequest.state.in_(states))
    if sources:
        predicates.append(AlertEvaluationRequest.active_source.in_(sources))
    if item_id is not None:
        predicates.append(AlertEvaluationRequest.item_id == item_id)
    if needs_attention:
        predicates.append(
            or_(
                AlertEvaluationRequest.state.in_(["retry_wait", "dead_letter"]),
                and_(
                    AlertEvaluationRequest.state == "pending",
                    AlertEvaluationRequest.dispatch_failure_count > 0,
                ),
                and_(
                    AlertEvaluationRequest.state == "processing",
                    AlertEvaluationRequest.dispatch_failure_count > 0,
                    AlertEvaluationRequest.last_dispatch_failed_at.is_not(None),
                    or_(
                        AlertEvaluationRequest.claimed_at.is_(None),
                        AlertEvaluationRequest.last_dispatch_failed_at
                        >= AlertEvaluationRequest.claimed_at,
                    ),
                    or_(
                        AlertEvaluationRequest.lease_expires_at.is_(None),
                        AlertEvaluationRequest.lease_expires_at <= current_time,
                    ),
                ),
            )
        )
    total = (
        db.scalar(select(func.count(AlertEvaluationRequest.id)).where(*predicates)) or 0
    )
    items = list(
        db.scalars(
            select(AlertEvaluationRequest)
            .where(*predicates)
            .order_by(
                AlertEvaluationRequest.created_at.desc(),
                AlertEvaluationRequest.id.desc(),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    )
    return AlertEvaluationRequestPage(items, int(total), page, page_size)


def get_alert_evaluation_request(
    db: Session,
    *,
    request_id: uuid.UUID,
    for_update: bool = False,
) -> AlertEvaluationRequest:
    query = select(AlertEvaluationRequest).where(
        AlertEvaluationRequest.id == request_id
    )
    if for_update:
        query = query.with_for_update().execution_options(populate_existing=True)
    request = db.scalar(query)
    if request is None:
        raise AlertEvaluationNotFoundError("Alert evaluation request not found.")
    return request


def list_alert_evaluation_activity(
    db: Session,
    *,
    request_id: uuid.UUID,
    page: int,
    page_size: int,
) -> AlertEvaluationActivityPage:
    get_alert_evaluation_request(db, request_id=request_id)
    predicate = AlertEvaluationRequestActivity.request_id == request_id
    total = (
        db.scalar(
            select(func.count(AlertEvaluationRequestActivity.id)).where(predicate)
        )
        or 0
    )
    items = list(
        db.scalars(
            select(AlertEvaluationRequestActivity)
            .where(predicate)
            .order_by(
                AlertEvaluationRequestActivity.created_at.desc(),
                AlertEvaluationRequestActivity.id.desc(),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    )
    return AlertEvaluationActivityPage(items, int(total), page, page_size)


def replay_dead_letter_evaluation(
    db: Session,
    *,
    request_id: uuid.UUID,
    expected_version: int,
    actor_user_id: uuid.UUID,
    now: datetime | None = None,
) -> AlertEvaluationRequest:
    current_time = _as_utc(now or datetime.now(timezone.utc))
    locked = lock_alert_evaluation_item_and_request(db, request_id=request_id)
    request = locked.request
    if request is None:
        raise AlertEvaluationNotFoundError("Alert evaluation request not found.")
    if request.version != expected_version:
        raise AlertEvaluationConflictError(
            (
                f"Alert evaluation changed since it was loaded: expected version "
                f"{expected_version}, current version is {request.version}. Refresh and retry."
            ),
            code="alert_evaluation_version_conflict",
            current_version=request.version,
        )
    if request.state != "dead_letter":
        raise AlertEvaluationConflictError(
            "Only dead-letter alert evaluations can be replayed.",
            code="alert_evaluation_not_dead_letter",
            current_version=request.version,
        )
    item = locked.item
    if item is None:
        raise AlertEvaluationValidationError(
            "The source item no longer exists, so this evaluation cannot be replayed."
        )
    if item.content_hash != request.item_content_hash:
        raise AlertEvaluationValidationError(
            "The source item content changed; create a new evaluation intent instead of replaying this version."
        )

    previous_error_code = request.last_error_code
    request.state = "pending"
    request.active_source = "replay"
    request.attempt_count = 0
    request.available_at = current_time
    request.dispatch_claimed_at = current_time
    request.dispatch_published_at = None
    request.dispatch_attempt_count = (
        max(0, int(request.dispatch_attempt_count or 0)) + 1
    )
    request.claimed_at = None
    request.lease_token = None
    request.lease_expires_at = None
    request.completed_at = None
    request.last_error_code = None
    request.last_error_message = None
    request.last_replayed_at = current_time
    request.version = max(1, int(request.version or 1)) + 1
    db.add(request)
    record_alert_evaluation_activity(
        db,
        request_id=request.id,
        actor_user_id=actor_user_id,
        action="replay_requested",
        details={
            "original_source": request.source,
            "notify": request.notify,
            "previous_error_code": previous_error_code,
        },
    )
    db.flush()
    return request


def list_alert_occurrence_metrics(
    db: Session,
    *,
    owner_user_id: uuid.UUID,
    since: datetime,
    until: datetime,
    severities: list[str],
    lifecycle_states: list[str],
    suppressed: bool | None,
    limit: int,
) -> AlertOccurrenceMetricPage:
    window_start, window_end = _utc_day_window(since, until)
    predicates = [
        AlertOccurrenceMetric.owner_user_id == owner_user_id,
        AlertOccurrenceMetric.bucket_start >= window_start,
        AlertOccurrenceMetric.bucket_start < window_end,
    ]
    if severities:
        predicates.append(AlertOccurrenceMetric.severity.in_(severities))
    if lifecycle_states:
        predicates.append(AlertOccurrenceMetric.lifecycle_state.in_(lifecycle_states))
    if suppressed is not None:
        predicates.append(AlertOccurrenceMetric.suppressed == suppressed)
    bounded_limit = max(1, min(int(limit), 1_000))
    historical = list(
        db.scalars(select(AlertOccurrenceMetric).where(*predicates)).all()
    )
    suppressed_dimension = case(
        (AlertOccurrence.suppressed_at.is_not(None), True),
        else_=False,
    )
    bucket = func.date_trunc("day", AlertOccurrence.created_at, "UTC")
    live_predicates = [
        AlertOccurrence.owner_user_id == owner_user_id,
        AlertOccurrence.metrics_aggregated_at.is_(None),
        AlertOccurrence.created_at >= window_start,
        AlertOccurrence.created_at < window_end,
    ]
    if severities:
        live_predicates.append(AlertOccurrence.severity_snapshot.in_(severities))
    if lifecycle_states:
        live_predicates.append(AlertOccurrence.lifecycle_state.in_(lifecycle_states))
    if suppressed is not None:
        live_predicates.append(
            AlertOccurrence.suppressed_at.is_not(None)
            if suppressed
            else AlertOccurrence.suppressed_at.is_(None)
        )
    live_rows = db.execute(
        select(
            bucket.label("bucket_start"),
            AlertOccurrence.severity_snapshot,
            AlertOccurrence.lifecycle_state,
            suppressed_dimension.label("suppressed"),
            func.count(AlertOccurrence.id),
            func.min(AlertOccurrence.created_at),
            func.max(AlertOccurrence.updated_at),
        )
        .where(*live_predicates)
        .group_by(
            bucket,
            AlertOccurrence.severity_snapshot,
            AlertOccurrence.lifecycle_state,
            suppressed_dimension,
        )
    ).all()
    combined: dict[tuple[datetime, str, str, bool], AlertOccurrenceMetricPoint] = {}
    for row in historical:
        bucket_start = _as_utc(row.bucket_start)
        key = (bucket_start, row.severity, row.lifecycle_state, row.suppressed)
        combined[key] = AlertOccurrenceMetricPoint(
            row.id,
            bucket_start,
            owner_user_id,
            row.severity,
            row.lifecycle_state,
            row.suppressed,
            row.occurrence_count,
            row.created_at,
            row.updated_at,
        )
    for (
        bucket_start,
        severity,
        state,
        is_suppressed,
        count,
        created,
        updated,
    ) in live_rows:
        bucket_start = _as_utc(bucket_start)
        key = (bucket_start, severity, state, is_suppressed)
        previous = combined.get(key)
        combined[key] = AlertOccurrenceMetricPoint(
            previous.id if previous else _metric_projection_id(owner_user_id, key),
            bucket_start,
            owner_user_id,
            severity,
            state,
            is_suppressed,
            int(count) + (previous.occurrence_count if previous else 0),
            min(created, previous.created_at) if previous else created,
            max(updated, previous.updated_at) if previous else updated,
        )
    ordered = sorted(
        combined.values(),
        key=lambda row: (row.bucket_start, row.id),
        reverse=True,
    )
    return AlertOccurrenceMetricPage(
        ordered[:bounded_limit],
        truncated=len(ordered) > bounded_limit,
    )


def _metric_projection_id(
    owner_user_id: uuid.UUID,
    key: tuple[datetime, str, str, bool],
) -> uuid.UUID:
    return uuid.uuid5(owner_user_id, "|".join(str(value) for value in key))


def _utc_day_window(since: datetime, until: datetime) -> tuple[datetime, datetime]:
    start = _as_utc(since).replace(hour=0, minute=0, second=0, microsecond=0)
    final_day = _as_utc(until).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, final_day + timedelta(days=1)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
