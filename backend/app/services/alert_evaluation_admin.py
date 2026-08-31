from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, case, exists, false, func, or_, select, true, union_all
from sqlalchemy.orm import Session, aliased

from app.models.alert_evaluation_request import (
    AlertEvaluationRequest,
    AlertEvaluationRequestActivity,
)
from app.models.alert_occurrence import (
    AlertOccurrence,
    AlertOccurrenceMetric,
    AlertOccurrenceMetricCohort,
    AlertOccurrenceMetricCohortLabel,
)
from app.models.data_policy import HandlingLabel
from app.models.feed import Feed
from app.models.item import Item
from app.services.alert_acceptance import lock_alert_evaluation_item_and_request
from app.services.alert_evaluation_history import record_alert_evaluation_activity
from app.services.data_access_envelopes import (
    DATA_ACCESS_RESOURCE_ALERT_OCCURRENCE,
    data_access_envelope_predicate,
)
from app.services.data_access_policy import (
    DataAccessContext,
    handling_label_access_predicate,
)


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
    data_access: DataAccessContext,
    states: list[str],
    sources: list[str],
    item_id: uuid.UUID | None,
    page: int,
    page_size: int,
    needs_attention: bool = False,
    now: datetime | None = None,
) -> AlertEvaluationRequestPage:
    current_time = _as_utc(now or datetime.now(timezone.utc))
    predicates = [_alert_evaluation_data_access_predicate(data_access)]
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
    data_access: DataAccessContext,
    for_update: bool = False,
) -> AlertEvaluationRequest:
    query = select(AlertEvaluationRequest).where(
        AlertEvaluationRequest.id == request_id,
        _alert_evaluation_data_access_predicate(data_access),
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
    data_access: DataAccessContext,
    page: int,
    page_size: int,
) -> AlertEvaluationActivityPage:
    get_alert_evaluation_request(
        db,
        request_id=request_id,
        data_access=data_access,
    )
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
    data_access: DataAccessContext,
    expected_version: int,
    actor_user_id: uuid.UUID,
    now: datetime | None = None,
) -> AlertEvaluationRequest:
    current_time = _as_utc(now or datetime.now(timezone.utc))
    get_alert_evaluation_request(
        db,
        request_id=request_id,
        data_access=data_access,
    )
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
    data_access: DataAccessContext,
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
    if data_access.enforced:
        historical = (
            select(
                AlertOccurrenceMetric.bucket_start.label("bucket_start"),
                AlertOccurrenceMetric.severity.label("severity"),
                AlertOccurrenceMetric.lifecycle_state.label("lifecycle_state"),
                AlertOccurrenceMetric.suppressed.label("suppressed"),
                func.sum(AlertOccurrenceMetricCohort.occurrence_count).label(
                    "occurrence_count"
                ),
                func.min(AlertOccurrenceMetric.created_at).label("created_at"),
                func.max(AlertOccurrenceMetric.updated_at).label("updated_at"),
            )
            .join(
                AlertOccurrenceMetricCohort,
                AlertOccurrenceMetricCohort.metric_id == AlertOccurrenceMetric.id,
            )
            .where(
                *predicates,
                _alert_metric_cohort_data_access_predicate(data_access),
            )
            .group_by(
                AlertOccurrenceMetric.bucket_start,
                AlertOccurrenceMetric.severity,
                AlertOccurrenceMetric.lifecycle_state,
                AlertOccurrenceMetric.suppressed,
            )
        )
    else:
        historical = select(
            AlertOccurrenceMetric.bucket_start.label("bucket_start"),
            AlertOccurrenceMetric.severity.label("severity"),
            AlertOccurrenceMetric.lifecycle_state.label("lifecycle_state"),
            AlertOccurrenceMetric.suppressed.label("suppressed"),
            AlertOccurrenceMetric.occurrence_count.label("occurrence_count"),
            AlertOccurrenceMetric.created_at.label("created_at"),
            AlertOccurrenceMetric.updated_at.label("updated_at"),
        ).where(*predicates)
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
        data_access_envelope_predicate(
            DATA_ACCESS_RESOURCE_ALERT_OCCURRENCE,
            AlertOccurrence.id,
            data_access,
        ),
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
    live = (
        select(
            bucket.label("bucket_start"),
            AlertOccurrence.severity_snapshot.label("severity"),
            AlertOccurrence.lifecycle_state.label("lifecycle_state"),
            suppressed_dimension.label("suppressed"),
            func.count(AlertOccurrence.id).label("occurrence_count"),
            func.min(AlertOccurrence.created_at).label("created_at"),
            func.max(AlertOccurrence.updated_at).label("updated_at"),
        )
        .where(*live_predicates)
        .group_by(
            bucket,
            AlertOccurrence.severity_snapshot,
            AlertOccurrence.lifecycle_state,
            suppressed_dimension,
        )
    )

    sources = union_all(historical, live).subquery("alert_metric_sources")
    rows = db.execute(
        select(
            sources.c.bucket_start,
            sources.c.severity,
            sources.c.lifecycle_state,
            sources.c.suppressed,
            func.sum(sources.c.occurrence_count).label("occurrence_count"),
            func.min(sources.c.created_at).label("created_at"),
            func.max(sources.c.updated_at).label("updated_at"),
        )
        .group_by(
            sources.c.bucket_start,
            sources.c.severity,
            sources.c.lifecycle_state,
            sources.c.suppressed,
        )
        .order_by(
            sources.c.bucket_start.desc(),
            sources.c.severity.desc(),
            sources.c.lifecycle_state.asc(),
            sources.c.suppressed.asc(),
        )
        .limit(bounded_limit + 1)
    ).all()
    items = []
    for row in rows[:bounded_limit]:
        bucket_start = _as_utc(row.bucket_start)
        key = (
            bucket_start,
            row.severity,
            row.lifecycle_state,
            row.suppressed,
        )
        items.append(
            AlertOccurrenceMetricPoint(
                _metric_projection_id(owner_user_id, key),
                bucket_start,
                owner_user_id,
                row.severity,
                row.lifecycle_state,
                row.suppressed,
                int(row.occurrence_count),
                row.created_at,
                row.updated_at,
            )
        )
    return AlertOccurrenceMetricPage(
        items,
        truncated=len(rows) > bounded_limit,
    )


def _metric_projection_id(
    owner_user_id: uuid.UUID,
    key: tuple[datetime, str, str, bool],
) -> uuid.UUID:
    return uuid.uuid5(owner_user_id, "|".join(str(value) for value in key))


def _alert_evaluation_data_access_predicate(
    data_access: DataAccessContext,
):
    if not data_access.enforced:
        return true()
    return exists(
        select(Item.id)
        .join(Feed, Feed.id == Item.feed_id)
        .where(
            Item.id == AlertEvaluationRequest.item_id,
            handling_label_access_predicate(
                Feed.handling_label_id,
                data_access,
            ),
        )
    )


def _alert_metric_cohort_data_access_predicate(data_access: DataAccessContext):
    if not data_access.principal_eligible:
        return false()
    if not data_access.enforced:
        return true()
    metric_label = aliased(AlertOccurrenceMetricCohortLabel)
    handling_label = aliased(HandlingLabel)
    any_label = exists(
        select(metric_label.cohort_id).where(
            metric_label.cohort_id == AlertOccurrenceMetricCohort.id,
        )
    )
    inaccessible_label = exists(
        select(metric_label.cohort_id)
        .join(handling_label, handling_label.id == metric_label.label_id)
        .where(
            metric_label.cohort_id == AlertOccurrenceMetricCohort.id,
            or_(
                metric_label.label_id.not_in(data_access.allowed_label_ids),
                handling_label.is_active.is_(False),
            ),
        )
    )
    return and_(any_label, ~inaccessible_label)


def _utc_day_window(since: datetime, until: datetime) -> tuple[datetime, datetime]:
    start = _as_utc(since).replace(hour=0, minute=0, second=0, microsecond=0)
    final_day = _as_utc(until).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, final_day + timedelta(days=1)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
