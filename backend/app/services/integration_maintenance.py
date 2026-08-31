from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, exists, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.integration import (
    IntegrationAttempt,
    IntegrationDelivery,
    IntegrationDeliveryMetric,
    IntegrationEvent,
)
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.services.data_access_envelopes import (
    DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY,
    DATA_ACCESS_RESOURCE_INTEGRATION_EVENT,
)
from app.services.data_access_retention import (
    prune_deleted_resource_envelopes,
    prune_orphan_data_access_envelopes,
)

settings = get_settings()
TERMINAL_DELIVERY_STATES = ("succeeded", "failed", "dead_letter")
MAX_INTEGRATION_MAINTENANCE_BATCH_SIZE = 1_000


@dataclass(frozen=True)
class IntegrationMaintenanceResult:
    rolled_up: int
    webhook_deliveries_deleted: int
    deliveries_deleted: int
    events_deleted: int
    metrics_deleted: int
    data_access_envelopes_deleted: int
    data_access_envelope_candidates_scanned: int
    data_access_envelope_unknown_types: int
    data_access_envelope_backlog_remaining: bool


def run_integration_delivery_maintenance(
    db: Session,
    *,
    now: datetime | None = None,
    batch_size: int | None = None,
) -> IntegrationMaintenanceResult:
    current_time = now or datetime.now(timezone.utc)
    effective_batch_size = _maintenance_batch_size(batch_size)
    rolled_up = rollup_terminal_integration_deliveries(
        db,
        now=current_time,
        batch_size=effective_batch_size,
    )
    deleted = prune_integration_delivery_history(
        db,
        now=current_time,
        batch_size=effective_batch_size,
    )
    return IntegrationMaintenanceResult(rolled_up=rolled_up, **deleted)


def rollup_terminal_integration_deliveries(
    db: Session,
    *,
    now: datetime | None = None,
    batch_size: int | None = None,
) -> int:
    current_time = now or datetime.now(timezone.utc)
    cutoff = current_time - timedelta(
        seconds=max(0, int(settings.integration_delivery_metrics_delay_seconds))
    )
    effective_batch_size = _maintenance_batch_size(batch_size)
    terminal_at = _terminal_delivery_timestamp()
    deliveries = db.scalars(
        select(IntegrationDelivery)
        .where(
            IntegrationDelivery.state.in_(TERMINAL_DELIVERY_STATES),
            IntegrationDelivery.metrics_aggregated_at.is_(None),
            terminal_at <= cutoff,
        )
        .order_by(terminal_at.asc(), IntegrationDelivery.id.asc())
        .limit(effective_batch_size)
        .with_for_update(skip_locked=True)
    ).all()
    if not deliveries:
        return 0

    attempt_stats = _attempt_stats_by_delivery(
        db, [delivery.id for delivery in deliveries]
    )
    rollups: dict[tuple[datetime, uuid.UUID, str, str], dict[str, int]] = {}
    for delivery in deliveries:
        completed_at = _coerce_utc(
            delivery.completed_at or delivery.dead_lettered_at or delivery.updated_at
        )
        bucket_start = completed_at.replace(minute=0, second=0, microsecond=0)
        key = (
            bucket_start,
            delivery.integration_id,
            delivery.connector_type,
            delivery.event_type,
        )
        aggregate = rollups.setdefault(
            key,
            {
                "succeeded_count": 0,
                "failed_count": 0,
                "dead_letter_count": 0,
                "attempt_count": 0,
                "duration_total_ms": 0,
                "duration_max_ms": 0,
            },
        )
        aggregate[f"{delivery.state}_count"] += 1
        attempts, duration_total_ms, duration_max_ms = attempt_stats.get(
            delivery.id,
            (
                max(0, int(delivery.attempt_count or 0)),
                max(0, int(delivery.last_duration_ms or 0)),
                max(0, int(delivery.last_duration_ms or 0)),
            ),
        )
        aggregate["attempt_count"] += attempts
        aggregate["duration_total_ms"] += duration_total_ms
        aggregate["duration_max_ms"] = max(
            aggregate["duration_max_ms"], duration_max_ms
        )

    for (
        bucket_start,
        integration_id,
        connector_type,
        event_type,
    ), aggregate in rollups.items():
        statement = pg_insert(IntegrationDeliveryMetric).values(
            id=uuid.uuid4(),
            bucket_start=bucket_start,
            integration_id=integration_id,
            connector_type=connector_type,
            event_type=event_type,
            **aggregate,
            updated_at=current_time,
        )
        excluded = statement.excluded
        statement = statement.on_conflict_do_update(
            constraint="uq_integration_delivery_metrics_bucket_dimension",
            set_={
                "succeeded_count": IntegrationDeliveryMetric.succeeded_count
                + excluded.succeeded_count,
                "failed_count": IntegrationDeliveryMetric.failed_count
                + excluded.failed_count,
                "dead_letter_count": IntegrationDeliveryMetric.dead_letter_count
                + excluded.dead_letter_count,
                "attempt_count": IntegrationDeliveryMetric.attempt_count
                + excluded.attempt_count,
                "duration_total_ms": IntegrationDeliveryMetric.duration_total_ms
                + excluded.duration_total_ms,
                "duration_max_ms": func.greatest(
                    IntegrationDeliveryMetric.duration_max_ms,
                    excluded.duration_max_ms,
                ),
                "updated_at": current_time,
            },
        )
        db.execute(statement)

    for delivery in deliveries:
        delivery.metrics_aggregated_at = current_time
        db.add(delivery)
    db.commit()
    return len(deliveries)


def prune_integration_delivery_history(
    db: Session,
    *,
    now: datetime | None = None,
    batch_size: int | None = None,
) -> dict[str, int | bool]:
    current_time = now or datetime.now(timezone.utc)
    effective_batch_size = _maintenance_batch_size(batch_size)
    delivery_cutoff = current_time - timedelta(
        days=max(1, int(settings.integration_delivery_retention_days))
    )
    event_cutoff = current_time - timedelta(
        days=max(1, int(settings.integration_event_retention_days))
    )
    metrics_cutoff = current_time - timedelta(
        days=max(1, int(settings.integration_metrics_retention_days))
    )
    terminal_at = _terminal_delivery_timestamp()

    eligible_delivery_ids = list(
        db.scalars(
            select(IntegrationDelivery.id)
            .where(
                IntegrationDelivery.state.in_(TERMINAL_DELIVERY_STATES),
                IntegrationDelivery.metrics_aggregated_at.is_not(None),
                terminal_at < delivery_cutoff,
            )
            .order_by(terminal_at.asc())
            .limit(effective_batch_size)
        ).all()
    )
    webhook_deleted = 0
    deliveries_deleted = 0
    data_access_envelopes_deleted = 0
    if eligible_delivery_ids:
        webhook_result = db.execute(
            delete(NotificationWebhookDelivery)
            .where(
                NotificationWebhookDelivery.integration_delivery_id.in_(
                    eligible_delivery_ids
                )
            )
            .execution_options(synchronize_session=False)
        )
        webhook_deleted = int(webhook_result.rowcount or 0)
        delivery_result = db.execute(
            delete(IntegrationDelivery)
            .where(IntegrationDelivery.id.in_(eligible_delivery_ids))
            .execution_options(synchronize_session=False)
        )
        deliveries_deleted = int(delivery_result.rowcount or 0)
        if deliveries_deleted:
            data_access_envelopes_deleted += prune_deleted_resource_envelopes(
                db,
                resources=(
                    (DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY, delivery_id)
                    for delivery_id in eligible_delivery_ids
                ),
            )

    event_ids = list(
        db.scalars(
            select(IntegrationEvent.id)
            .where(
                IntegrationEvent.routing_state.in_(["routed", "dead_letter"]),
                IntegrationEvent.created_at < event_cutoff,
                ~exists(
                    select(IntegrationDelivery.id).where(
                        IntegrationDelivery.event_id == IntegrationEvent.id
                    )
                ),
            )
            .order_by(IntegrationEvent.created_at.asc())
            .limit(effective_batch_size)
        ).all()
    )
    events_deleted = 0
    if event_ids:
        event_result = db.execute(
            delete(IntegrationEvent)
            .where(IntegrationEvent.id.in_(event_ids))
            .execution_options(synchronize_session=False)
        )
        events_deleted = int(event_result.rowcount or 0)
        if events_deleted:
            data_access_envelopes_deleted += prune_deleted_resource_envelopes(
                db,
                resources=(
                    (DATA_ACCESS_RESOURCE_INTEGRATION_EVENT, event_id)
                    for event_id in event_ids
                ),
            )

    metric_ids = list(
        db.scalars(
            select(IntegrationDeliveryMetric.id)
            .where(IntegrationDeliveryMetric.bucket_start < metrics_cutoff)
            .order_by(IntegrationDeliveryMetric.bucket_start.asc())
            .limit(effective_batch_size)
        ).all()
    )
    metrics_deleted = 0
    if metric_ids:
        metric_result = db.execute(
            delete(IntegrationDeliveryMetric)
            .where(IntegrationDeliveryMetric.id.in_(metric_ids))
            .execution_options(synchronize_session=False)
        )
        metrics_deleted = int(metric_result.rowcount or 0)
    orphan_result = prune_orphan_data_access_envelopes(
        db,
        limit=effective_batch_size,
    )
    data_access_envelopes_deleted += orphan_result.deleted_count
    db.commit()
    return {
        "webhook_deliveries_deleted": webhook_deleted,
        "deliveries_deleted": deliveries_deleted,
        "events_deleted": events_deleted,
        "metrics_deleted": metrics_deleted,
        "data_access_envelopes_deleted": data_access_envelopes_deleted,
        "data_access_envelope_candidates_scanned": orphan_result.candidates_scanned,
        "data_access_envelope_unknown_types": orphan_result.unknown_resource_types,
        "data_access_envelope_backlog_remaining": orphan_result.backlog_remaining,
    }


def _attempt_stats_by_delivery(
    db: Session,
    delivery_ids: list[uuid.UUID],
) -> dict[uuid.UUID, tuple[int, int, int]]:
    rows = db.execute(
        select(
            IntegrationAttempt.delivery_id,
            func.count(IntegrationAttempt.id),
            func.coalesce(func.sum(IntegrationAttempt.duration_ms), 0),
            func.coalesce(func.max(IntegrationAttempt.duration_ms), 0),
        )
        .where(IntegrationAttempt.delivery_id.in_(delivery_ids))
        .group_by(IntegrationAttempt.delivery_id)
    ).all()
    return {
        delivery_id: (int(attempts), int(duration_total_ms), int(duration_max_ms))
        for delivery_id, attempts, duration_total_ms, duration_max_ms in rows
    }


def _terminal_delivery_timestamp():
    return func.coalesce(
        IntegrationDelivery.completed_at,
        IntegrationDelivery.dead_lettered_at,
        IntegrationDelivery.updated_at,
    )


def _maintenance_batch_size(value: int | None) -> int:
    configured = value or settings.integration_delivery_maintenance_batch_size
    return max(1, min(int(configured), MAX_INTEGRATION_MAINTENANCE_BATCH_SIZE))


def _coerce_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
