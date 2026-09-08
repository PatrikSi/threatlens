from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, literal, select
from sqlalchemy.orm import Session, aliased

from app.models.action_approval import ActionExecutionReceipt
from app.models.ai_task_event import AITaskEvent
from app.models.ai_task_run import AITaskRun
from app.models.alert_evaluation_match import AlertEvaluationMatch
from app.models.alert_evaluation_request import AlertEvaluationRequestActivity
from app.models.alert_occurrence import (
    AlertOccurrence,
    AlertOccurrenceMetricCohort,
    AlertOccurrenceMetricCohortCapturedLabel,
    AlertOccurrenceMetricCohortLabel,
    AlertOccurrenceMetricCohortTaintLabel,
)
from app.models.audit_log import AuditLogDataAccessFeed, AuditLogDataAccessLabel
from app.models.governance_operation_receipt import GovernanceOperationReceipt
from app.models.integration import (
    IntegrationAttempt,
    IntegrationDelivery,
    IntegrationDeliveryMetricCohort,
    IntegrationDeliveryMetricCohortCapturedLabel,
    IntegrationDeliveryMetricCohortFeed,
    IntegrationDeliveryMetricCohortLabel,
    IntegrationDeliveryMetricCohortTaintLabel,
)
from app.models.mfa import UserTOTPCredential
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.models.report import Report


MAX_LIFECYCLE_DEPENDENT_ROWS_PER_BATCH = 10_000
MAX_LIFECYCLE_PARENT_SCAN = 1_000
_DEPENDENT_TABLES = frozenset(
    {
        "action_approval_requests",
        "ai_task_runs",
        "alert_evaluation_requests",
        "alert_occurrence_metrics",
        "audit_logs",
        "auth_sessions",
        "integration_deliveries",
        "integration_delivery_metrics",
        "integration_events",
        "notification_webhook_deliveries",
    }
)


@dataclass(frozen=True)
class LifecycleDependencySelection:
    ids: list[uuid.UUID]
    dependent_rows: int
    oversized_count: int
    budget_exhausted: bool


def has_lifecycle_dependants(model) -> bool:
    return model.__table__.name in _DEPENDENT_TABLES


def select_with_dependent_budget(
    db: Session,
    *,
    model,
    candidate_ids: list[uuid.UUID],
    max_dependent_rows: int = MAX_LIFECYCLE_DEPENDENT_ROWS_PER_BATCH,
    max_parent_records: int | None = None,
) -> LifecycleDependencySelection:
    bounded_budget = max(1, int(max_dependent_rows))
    counts = lifecycle_dependent_row_counts(
        db,
        model=model,
        parent_ids=candidate_ids,
        max_rows_per_parent=bounded_budget,
    )
    selected: list[uuid.UUID] = []
    consumed = 0
    oversized = 0
    budget_exhausted = False
    for parent_id in candidate_ids:
        row_cost = int(counts.get(parent_id, 0))
        if row_cost > bounded_budget:
            oversized += 1
            continue
        if max_parent_records is not None and len(selected) >= max_parent_records:
            continue
        if consumed + row_cost > bounded_budget:
            budget_exhausted = True
            continue
        selected.append(parent_id)
        consumed += row_cost
    return LifecycleDependencySelection(
        ids=selected,
        dependent_rows=consumed,
        oversized_count=oversized,
        budget_exhausted=budget_exhausted,
    )


def lifecycle_parent_scan_limit(requested_records: int) -> int:
    requested = max(1, int(requested_records))
    return min(MAX_LIFECYCLE_PARENT_SCAN, max(requested, requested * 4))


def partition_oversized_lifecycle_parents(
    db: Session,
    *,
    model,
    parent_ids: list[uuid.UUID],
    max_dependent_rows: int = MAX_LIFECYCLE_DEPENDENT_ROWS_PER_BATCH,
) -> tuple[set[uuid.UUID], set[uuid.UUID]]:
    bounded_budget = max(1, int(max_dependent_rows))
    counts = lifecycle_dependent_row_counts(
        db,
        model=model,
        parent_ids=parent_ids,
        max_rows_per_parent=bounded_budget,
    )
    oversized = {
        parent_id
        for parent_id, count in counts.items()
        if count > bounded_budget
    }
    return set(parent_ids) - oversized, oversized


def lifecycle_dependent_row_counts(
    db: Session,
    *,
    model,
    parent_ids: list[uuid.UUID],
    max_rows_per_parent: int = MAX_LIFECYCLE_DEPENDENT_ROWS_PER_BATCH,
) -> dict[uuid.UUID, int]:
    counts = {parent_id: 0 for parent_id in parent_ids}
    if not parent_ids:
        return counts
    cap = max(1, int(max_rows_per_parent))
    table_name = model.__table__.name
    if table_name == "audit_logs":
        _add_capped_direct_counts(
            db,
            counts,
            parent_model=model,
            parent_column=AuditLogDataAccessFeed.audit_log_id,
            cap=cap,
        )
        _add_capped_direct_counts(
            db,
            counts,
            parent_model=model,
            parent_column=AuditLogDataAccessLabel.audit_log_id,
            cap=cap,
        )
    elif table_name == "action_approval_requests":
        _add_capped_direct_counts(
            db,
            counts,
            parent_model=model,
            parent_column=ActionExecutionReceipt.approval_request_id,
            cap=cap,
        )
        _add_capped_query_counts(
            db,
            counts,
            parent_model=model,
            row_query=select(literal(1)).where(
                GovernanceOperationReceipt.resource_type == "action_approval",
                GovernanceOperationReceipt.resource_id == model.id,
            )
            .correlate(model),
            cap=cap,
        )
    elif table_name == "ai_task_runs":
        _add_capped_direct_counts(
            db,
            counts,
            parent_model=model,
            parent_column=AITaskEvent.task_run_id,
            cap=cap,
        )
        child_run = aliased(AITaskRun)
        _add_capped_query_counts(
            db,
            counts,
            parent_model=model,
            row_query=select(literal(1))
            .where(child_run.parent_run_id == model.id)
            .correlate(model),
            cap=cap,
        )
        superseding_run = aliased(AITaskRun)
        _add_capped_query_counts(
            db,
            counts,
            parent_model=model,
            row_query=select(literal(1))
            .where(superseding_run.superseded_by_task_run_id == model.id)
            .correlate(model),
            cap=cap,
        )
        _add_capped_direct_counts(
            db,
            counts,
            parent_model=model,
            parent_column=Report.request_task_run_id,
            cap=cap,
        )
    elif table_name == "auth_sessions":
        _add_capped_direct_counts(
            db,
            counts,
            parent_model=model,
            parent_column=UserTOTPCredential.enrollment_session_id,
            cap=cap,
        )
    elif table_name == "integration_deliveries":
        _add_capped_direct_counts(
            db,
            counts,
            parent_model=model,
            parent_column=IntegrationAttempt.delivery_id,
            cap=cap,
        )
        child_delivery = aliased(IntegrationDelivery)
        _add_capped_query_counts(
            db,
            counts,
            parent_model=model,
            row_query=select(literal(1))
            .where(child_delivery.source_delivery_id == model.id)
            .correlate(model),
            cap=cap,
        )
        _add_capped_direct_counts(
            db,
            counts,
            parent_model=model,
            parent_column=NotificationWebhookDelivery.integration_delivery_id,
            cap=cap,
        )
    elif table_name == "notification_webhook_deliveries":
        child_webhook = aliased(NotificationWebhookDelivery)
        _add_capped_query_counts(
            db,
            counts,
            parent_model=model,
            row_query=select(literal(1))
            .where(child_webhook.source_delivery_id == model.id)
            .correlate(model),
            cap=cap,
        )
    elif table_name == "integration_events":
        _add_capped_direct_counts(
            db,
            counts,
            parent_model=model,
            parent_column=IntegrationDelivery.event_id,
            cap=cap,
        )
        _add_capped_direct_counts(
            db,
            counts,
            parent_model=model,
            parent_column=AlertOccurrence.integration_event_id,
            cap=cap,
        )
    elif table_name == "integration_delivery_metrics":
        _integration_metric_counts(db, counts, model=model, cap=cap)
    elif table_name == "alert_evaluation_requests":
        _add_capped_direct_counts(
            db,
            counts,
            parent_model=model,
            parent_column=AlertEvaluationRequestActivity.request_id,
            cap=cap,
        )
        _add_capped_direct_counts(
            db,
            counts,
            parent_model=model,
            parent_column=AlertEvaluationMatch.request_id,
            cap=cap,
        )
    elif table_name == "alert_occurrence_metrics":
        _alert_metric_counts(db, counts, model=model, cap=cap)
    return counts


def _integration_metric_counts(
    db: Session,
    counts: dict[uuid.UUID, int],
    *,
    model,
    cap: int,
) -> None:
    _add_capped_direct_counts(
        db,
        counts,
        parent_model=model,
        parent_column=IntegrationDeliveryMetricCohort.metric_id,
        cap=cap,
    )
    for child_model in (
        IntegrationDeliveryMetricCohortLabel,
        IntegrationDeliveryMetricCohortCapturedLabel,
        IntegrationDeliveryMetricCohortTaintLabel,
        IntegrationDeliveryMetricCohortFeed,
    ):
        _add_capped_query_counts(
            db,
            counts,
            parent_model=model,
            row_query=select(literal(1))
            .join(
                child_model,
                child_model.cohort_id == IntegrationDeliveryMetricCohort.id,
            )
            .where(IntegrationDeliveryMetricCohort.metric_id == model.id)
            .correlate(model),
            cap=cap,
        )


def _alert_metric_counts(
    db: Session,
    counts: dict[uuid.UUID, int],
    *,
    model,
    cap: int,
) -> None:
    _add_capped_direct_counts(
        db,
        counts,
        parent_model=model,
        parent_column=AlertOccurrenceMetricCohort.metric_id,
        cap=cap,
    )
    for child_model in (
        AlertOccurrenceMetricCohortLabel,
        AlertOccurrenceMetricCohortCapturedLabel,
        AlertOccurrenceMetricCohortTaintLabel,
    ):
        _add_capped_query_counts(
            db,
            counts,
            parent_model=model,
            row_query=select(literal(1))
            .join(
                child_model,
                child_model.cohort_id == AlertOccurrenceMetricCohort.id,
            )
            .where(AlertOccurrenceMetricCohort.metric_id == model.id)
            .correlate(model),
            cap=cap,
        )


def _add_capped_direct_counts(
    db: Session,
    counts: dict[uuid.UUID, int],
    *,
    parent_model,
    parent_column,
    cap: int,
) -> None:
    _add_capped_query_counts(
        db,
        counts,
        parent_model=parent_model,
        row_query=select(literal(1))
        .where(parent_column == parent_model.id)
        .correlate(parent_model),
        cap=cap,
    )


def _add_capped_query_counts(
    db: Session,
    counts: dict[uuid.UUID, int],
    *,
    parent_model,
    row_query,
    cap: int,
) -> None:
    limited_rows = row_query.limit(cap + 1).subquery()
    capped_count = (
        select(func.count()).select_from(limited_rows).scalar_subquery()
    )
    rows = db.execute(
        select(parent_model.id, capped_count).where(
            parent_model.id.in_(tuple(counts))
        )
    ).all()
    _merge_counts(counts, rows)


def _merge_counts(counts: dict, rows) -> None:
    for parent_id, count in rows:
        if parent_id in counts:
            counts[parent_id] += int(count or 0)


__all__ = [
    "MAX_LIFECYCLE_DEPENDENT_ROWS_PER_BATCH",
    "MAX_LIFECYCLE_PARENT_SCAN",
    "LifecycleDependencySelection",
    "has_lifecycle_dependants",
    "lifecycle_dependent_row_counts",
    "lifecycle_parent_scan_limit",
    "partition_oversized_lifecycle_parents",
    "select_with_dependent_budget",
]
