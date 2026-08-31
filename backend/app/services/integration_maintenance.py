from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

from sqlalchemy import String, cast, delete, exists, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, aliased

from app.core.config import get_settings
from app.models.data_policy import (
    DataAccessEnvelope,
    DataAccessEnvelopeLabel,
    DataAccessEnvelopeSource,
    QUARANTINE_HANDLING_LABEL_ID,
    UNRESTRICTED_HANDLING_LABEL_ID,
)
from app.models.integration import (
    IntegrationAttempt,
    IntegrationDelivery,
    IntegrationDeliveryMetric,
    IntegrationDeliveryMetricCohort,
    IntegrationDeliveryMetricCohortCapturedLabel,
    IntegrationDeliveryMetricCohortFeed,
    IntegrationDeliveryMetricCohortLabel,
    IntegrationDeliveryMetricCohortTaintLabel,
    IntegrationEvent,
)
from app.models.notification_webhook_delivery import NotificationWebhookDelivery
from app.services.data_access_envelopes import (
    DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY,
    DATA_ACCESS_RESOURCE_INTEGRATION_EVENT,
    DataAccessEnvelopeSnapshot,
    DataAccessSourceSnapshot,
    get_data_access_envelope,
    get_data_access_envelope_sources,
)
from app.services.data_access_retention import (
    prune_deleted_resource_envelopes,
    prune_orphan_data_access_envelopes,
)
from app.services.data_access_runtime import (
    ensure_integration_delivery_data_access_envelope,
    lock_data_policy_revision_for_derivation,
)
from app.services.integration_metric_data_policy import (
    integration_metric_policy_cohort_key,
)

settings = get_settings()
TERMINAL_DELIVERY_STATES = ("succeeded", "failed", "dead_letter")
TERMINAL_NOTIFICATION_DELIVERY_STATES = ("succeeded", "failed")
ACTIVE_NOTIFICATION_DELIVERY_STATES = ("pending", "sending")
MAX_INTEGRATION_MAINTENANCE_BATCH_SIZE = 1_000
_FEED_PROVENANCE_SOURCE_TYPES = frozenset({"feed", "feed_taint", "item"})
_CAPTURED_PROVENANCE_SOURCE_TYPES = frozenset({"feed", "item", "system"})
_METRIC_COUNTER_FIELDS = (
    "succeeded_count",
    "failed_count",
    "dead_letter_count",
    "attempt_count",
    "duration_total_ms",
    "duration_max_ms",
)


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


@dataclass(frozen=True)
class _IntegrationMetricProvenance:
    captured_policy_revision: int
    provenance_complete: bool
    source_count: int
    captured_label_ids: frozenset[uuid.UUID]
    taint_label_ids: frozenset[uuid.UUID]
    source_feed_ids: frozenset[uuid.UUID]


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
    policy_revision = lock_data_policy_revision_for_derivation(db)
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
        db.commit()
        return 0

    attempt_stats = _attempt_stats_by_delivery(
        db, [delivery.id for delivery in deliveries]
    )
    public_rollups: dict[tuple[datetime, uuid.UUID, str, str], dict[str, int]] = {}
    cohort_rollups: dict[
        tuple[tuple[datetime, uuid.UUID, str, str], str], dict[str, int]
    ] = {}
    cohort_metadata: dict[
        tuple[tuple[datetime, uuid.UUID, str, str], str],
        _IntegrationMetricProvenance,
    ] = {}
    for delivery in deliveries:
        ensure_integration_delivery_data_access_envelope(
            db,
            delivery_id=delivery.id,
            expected_policy_revision=policy_revision,
        )
        envelope = get_data_access_envelope(
            db,
            resource_type=DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY,
            resource_id=delivery.id,
            for_update=True,
        )
        if envelope is None:
            raise RuntimeError(
                "Integration delivery metric rollup could not lock its data-policy "
                "envelope."
            )
        sources = get_data_access_envelope_sources(
            db,
            resource_type=DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY,
            resource_id=delivery.id,
            for_update=True,
        )
        provenance = _metric_provenance(
            envelope,
            sources,
            policy_revision=policy_revision,
        )
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
        aggregate = _delivery_metric_counts(
            delivery,
            attempt_stats=attempt_stats,
        )
        _merge_metric_counts(
            public_rollups.setdefault(key, _empty_metric_counts()),
            aggregate,
        )
        policy_cohort_key = integration_metric_policy_cohort_key(
            policy_revision=provenance.captured_policy_revision,
            provenance_complete=provenance.provenance_complete,
            source_count=provenance.source_count,
            label_ids=provenance.captured_label_ids,
            feed_ids=provenance.source_feed_ids,
        )
        cohort_identity = (key, policy_cohort_key)
        previous_metadata = cohort_metadata.setdefault(cohort_identity, provenance)
        if (
            previous_metadata.captured_policy_revision
            != provenance.captured_policy_revision
            or previous_metadata.provenance_complete != provenance.provenance_complete
            or previous_metadata.source_count != provenance.source_count
            or previous_metadata.captured_label_ids != provenance.captured_label_ids
            or previous_metadata.source_feed_ids != provenance.source_feed_ids
        ):
            raise RuntimeError(
                "Integration delivery metric cohort identity collision detected."
            )
        cohort_metadata[cohort_identity] = replace(
            previous_metadata,
            taint_label_ids=(
                previous_metadata.taint_label_ids | provenance.taint_label_ids
            ),
        )
        _merge_metric_counts(
            cohort_rollups.setdefault(cohort_identity, _empty_metric_counts()),
            aggregate,
        )

    db.execute(
        text(
            "SELECT set_config("
            "'threatlens.integration_metric_cohort_write', 'on', true)"
        )
    )
    metric_ids: dict[tuple[datetime, uuid.UUID, str, str], uuid.UUID] = {}
    for (
        bucket_start,
        integration_id,
        connector_type,
        event_type,
    ), aggregate in sorted(
        public_rollups.items(), key=lambda value: _metric_key_sort(value[0])
    ):
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
        metric_id = db.scalar(statement.returning(IntegrationDeliveryMetric.id))
        if metric_id is None:
            raise RuntimeError(
                "Integration delivery metric rollup did not return a base row."
            )
        metric_ids[(bucket_start, integration_id, connector_type, event_type)] = (
            metric_id
        )

    for cohort_identity, aggregate in sorted(
        cohort_rollups.items(),
        key=lambda value: (*_metric_key_sort(value[0][0]), value[0][1]),
    ):
        metric_key, policy_cohort_key = cohort_identity
        provenance = cohort_metadata[cohort_identity]
        captured_label_ids = provenance.captured_label_ids
        taint_label_ids = provenance.taint_label_ids
        feed_ids = provenance.source_feed_ids
        statement = pg_insert(IntegrationDeliveryMetricCohort).values(
            id=uuid.uuid4(),
            metric_id=metric_ids[metric_key],
            policy_cohort_key=policy_cohort_key,
            captured_policy_revision=provenance.captured_policy_revision,
            provenance_complete=provenance.provenance_complete,
            source_count=provenance.source_count,
            **aggregate,
            updated_at=current_time,
        )
        excluded = statement.excluded
        cohort_row = db.execute(
            statement.on_conflict_do_update(
                constraint="uq_integration_delivery_metric_cohorts_dimensions",
                set_={
                    "succeeded_count": (
                        IntegrationDeliveryMetricCohort.succeeded_count
                        + excluded.succeeded_count
                    ),
                    "failed_count": IntegrationDeliveryMetricCohort.failed_count
                    + excluded.failed_count,
                    "dead_letter_count": (
                        IntegrationDeliveryMetricCohort.dead_letter_count
                        + excluded.dead_letter_count
                    ),
                    "attempt_count": IntegrationDeliveryMetricCohort.attempt_count
                    + excluded.attempt_count,
                    "duration_total_ms": (
                        IntegrationDeliveryMetricCohort.duration_total_ms
                        + excluded.duration_total_ms
                    ),
                    "duration_max_ms": func.greatest(
                        IntegrationDeliveryMetricCohort.duration_max_ms,
                        excluded.duration_max_ms,
                    ),
                    "updated_at": current_time,
                },
            ).returning(
                IntegrationDeliveryMetricCohort.id,
                IntegrationDeliveryMetricCohort.captured_policy_revision,
                IntegrationDeliveryMetricCohort.provenance_complete,
                IntegrationDeliveryMetricCohort.source_count,
            )
        ).one()
        cohort_id = cohort_row.id
        if (
            cohort_row.captured_policy_revision != provenance.captured_policy_revision
            or cohort_row.provenance_complete != provenance.provenance_complete
            or cohort_row.source_count != provenance.source_count
        ):
            raise RuntimeError(
                "Integration delivery metric cohort metadata conflicts with its "
                "immutable identity."
            )
        for label_id in sorted(captured_label_ids, key=str):
            db.execute(
                pg_insert(IntegrationDeliveryMetricCohortCapturedLabel)
                .values(cohort_id=cohort_id, label_id=label_id)
                .on_conflict_do_nothing()
            )
        for label_id in sorted(taint_label_ids, key=str):
            db.execute(
                pg_insert(IntegrationDeliveryMetricCohortTaintLabel)
                .values(cohort_id=cohort_id, label_id=label_id)
                .on_conflict_do_nothing()
            )
        for label_id in sorted(captured_label_ids | taint_label_ids, key=str):
            db.execute(
                pg_insert(IntegrationDeliveryMetricCohortLabel)
                .values(cohort_id=cohort_id, label_id=label_id)
                .on_conflict_do_nothing()
            )
        for feed_id in sorted(feed_ids, key=str):
            db.execute(
                pg_insert(IntegrationDeliveryMetricCohortFeed)
                .values(
                    cohort_id=cohort_id,
                    source_feed_id_snapshot=feed_id,
                )
                .on_conflict_do_nothing()
            )
        retained_captured = frozenset(
            db.scalars(
                select(IntegrationDeliveryMetricCohortCapturedLabel.label_id)
                .where(
                    IntegrationDeliveryMetricCohortCapturedLabel.cohort_id == cohort_id
                )
                .with_for_update()
            ).all()
        )
        retained_taints = frozenset(
            db.scalars(
                select(IntegrationDeliveryMetricCohortTaintLabel.label_id)
                .where(IntegrationDeliveryMetricCohortTaintLabel.cohort_id == cohort_id)
                .with_for_update()
            ).all()
        )
        retained_effective = frozenset(
            db.scalars(
                select(IntegrationDeliveryMetricCohortLabel.label_id)
                .where(IntegrationDeliveryMetricCohortLabel.cohort_id == cohort_id)
                .with_for_update()
            ).all()
        )
        retained_feeds = frozenset(
            db.scalars(
                select(IntegrationDeliveryMetricCohortFeed.source_feed_id_snapshot)
                .where(IntegrationDeliveryMetricCohortFeed.cohort_id == cohort_id)
                .with_for_update()
            ).all()
        )
        if (
            retained_captured != captured_label_ids
            or not taint_label_ids.issubset(retained_taints)
            or retained_effective != retained_captured | retained_taints
            or retained_feeds != feed_ids
        ):
            raise RuntimeError(
                "Integration delivery metric cohort provenance is inconsistent."
            )

    _validate_metric_cohort_totals(db, set(metric_ids.values()))

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
    lock_data_policy_revision_for_derivation(db)

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
            .with_for_update(skip_locked=True)
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

    legacy_batch_size = max(0, effective_batch_size - len(eligible_delivery_ids))
    if legacy_batch_size:
        retry_child = aliased(NotificationWebhookDelivery)
        event_envelope = aliased(DataAccessEnvelope)
        event_source = aliased(DataAccessEnvelopeSource)
        event_label = aliased(DataAccessEnvelopeLabel)
        source_for_label = aliased(DataAccessEnvelopeSource)
        source_without_label = aliased(DataAccessEnvelopeSource)
        matching_label = aliased(DataAccessEnvelopeLabel)
        normalized_source_count = (
            select(func.count(event_source.id))
            .where(event_source.envelope_id == event_envelope.id)
            .correlate(event_envelope)
            .scalar_subquery()
        )
        max_source_revision = (
            select(func.max(event_source.captured_policy_revision))
            .where(event_source.envelope_id == event_envelope.id)
            .correlate(event_envelope)
            .scalar_subquery()
        )
        source_count_for_label = (
            select(func.count(source_for_label.id))
            .where(
                source_for_label.envelope_id == event_envelope.id,
                source_for_label.handling_label_id == event_label.label_id,
            )
            .correlate(event_envelope, event_label)
            .scalar_subquery()
        )
        mismatched_label_count = exists(
            select(event_label.label_id).where(
                event_label.envelope_id == event_envelope.id,
                event_label.source_count != source_count_for_label,
            )
        )
        source_missing_label = exists(
            select(source_without_label.id).where(
                source_without_label.envelope_id == event_envelope.id,
                ~exists(
                    select(matching_label.label_id).where(
                        matching_label.envelope_id == event_envelope.id,
                        matching_label.label_id
                        == source_without_label.handling_label_id,
                    )
                ),
            )
        )
        valid_event_envelope = exists(
            select(event_envelope.id).where(
                event_envelope.resource_type == DATA_ACCESS_RESOURCE_INTEGRATION_EVENT,
                event_envelope.resource_id == IntegrationEvent.id,
                event_envelope.source_count > 0,
                normalized_source_count == event_envelope.source_count,
                event_envelope.policy_revision >= max_source_revision,
                ~mismatched_label_count,
                ~source_missing_label,
            )
        )
        event_needs_lineage = exists(
            select(IntegrationEvent.id).where(
                IntegrationEvent.source_type == "notification_webhook_delivery",
                IntegrationEvent.source_id
                == cast(NotificationWebhookDelivery.id, String(36)),
                ~valid_event_envelope,
            )
        )
        active_retry_descendant = exists(
            select(retry_child.id).where(
                retry_child.source_delivery_id == NotificationWebhookDelivery.id,
                retry_child.delivery_state.in_(ACTIVE_NOTIFICATION_DELIVERY_STATES),
            )
        )
        legacy_delivery_ids = list(
            db.scalars(
                select(NotificationWebhookDelivery.id)
                .where(
                    NotificationWebhookDelivery.integration_delivery_id.is_(None),
                    NotificationWebhookDelivery.delivery_state.in_(
                        TERMINAL_NOTIFICATION_DELIVERY_STATES
                    ),
                    NotificationWebhookDelivery.attempted_at < delivery_cutoff,
                    ~active_retry_descendant,
                    ~event_needs_lineage,
                )
                .order_by(
                    NotificationWebhookDelivery.attempted_at.asc(),
                    NotificationWebhookDelivery.id.asc(),
                )
                .limit(legacy_batch_size)
                .with_for_update(skip_locked=True)
            ).all()
        )
        if legacy_delivery_ids:
            legacy_result = db.execute(
                delete(NotificationWebhookDelivery)
                .where(NotificationWebhookDelivery.id.in_(legacy_delivery_ids))
                .execution_options(synchronize_session=False)
            )
            webhook_deleted += int(legacy_result.rowcount or 0)

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
            .with_for_update(skip_locked=True)
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
            .with_for_update(skip_locked=True)
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


def _metric_provenance(
    envelope: DataAccessEnvelopeSnapshot,
    sources: tuple[DataAccessSourceSnapshot, ...],
    *,
    policy_revision: int,
) -> _IntegrationMetricProvenance:
    captured_sources = tuple(
        source for source in sources if source.source_type != "feed_taint"
    )
    taint_sources = tuple(
        source for source in sources if source.source_type == "feed_taint"
    )
    source_count = len(captured_sources)
    captured_label_ids = {source.handling_label_id for source in captured_sources}
    taint_label_ids = {source.handling_label_id for source in taint_sources}
    feed_ids = {
        source.source_feed_id
        for source in captured_sources
        if source.source_feed_id is not None
    }
    captured_policy_revision = max(
        (source.captured_policy_revision for source in captured_sources),
        default=policy_revision,
    )
    aggregate_complete = (
        envelope.source_count == len(sources)
        and envelope.policy_revision <= policy_revision
        and set(envelope.label_ids) == captured_label_ids | taint_label_ids
    )
    provenance_complete = (
        bool(captured_sources)
        and aggregate_complete
        and all(
            source.source_type in _CAPTURED_PROVENANCE_SOURCE_TYPES
            and source.captured_policy_revision <= policy_revision
            and (
                source.source_type not in _FEED_PROVENANCE_SOURCE_TYPES
                or source.source_feed_id is not None
            )
            and (
                source.source_type != "system"
                or (
                    source.source_feed_id is None
                    and source.handling_label_id == UNRESTRICTED_HANDLING_LABEL_ID
                )
            )
            for source in captured_sources
        )
    )
    if not provenance_complete:
        captured_label_ids.add(QUARANTINE_HANDLING_LABEL_ID)
    if not all(
        source.source_type == "feed_taint"
        and source.source_feed_id is not None
        and source.captured_policy_revision <= policy_revision
        for source in taint_sources
    ):
        taint_label_ids.add(QUARANTINE_HANDLING_LABEL_ID)
    return _IntegrationMetricProvenance(
        captured_policy_revision=captured_policy_revision,
        provenance_complete=provenance_complete,
        source_count=source_count,
        captured_label_ids=frozenset(captured_label_ids),
        taint_label_ids=frozenset(taint_label_ids),
        source_feed_ids=frozenset(feed_ids),
    )


def _empty_metric_counts() -> dict[str, int]:
    return {field: 0 for field in _METRIC_COUNTER_FIELDS}


def _delivery_metric_counts(
    delivery: IntegrationDelivery,
    *,
    attempt_stats: dict[uuid.UUID, tuple[int, int, int]],
) -> dict[str, int]:
    counts = _empty_metric_counts()
    counts[f"{delivery.state}_count"] = 1
    attempts, duration_total_ms, duration_max_ms = attempt_stats.get(
        delivery.id,
        (
            max(0, int(delivery.attempt_count or 0)),
            max(0, int(delivery.last_duration_ms or 0)),
            max(0, int(delivery.last_duration_ms or 0)),
        ),
    )
    counts["attempt_count"] = attempts
    counts["duration_total_ms"] = duration_total_ms
    counts["duration_max_ms"] = duration_max_ms
    return counts


def _merge_metric_counts(target: dict[str, int], increment: dict[str, int]) -> None:
    for field in _METRIC_COUNTER_FIELDS:
        if field == "duration_max_ms":
            target[field] = max(target[field], increment[field])
        else:
            target[field] += increment[field]


def _validate_metric_cohort_totals(
    db: Session,
    metric_ids: set[uuid.UUID],
) -> None:
    if not metric_ids:
        return
    totals = (
        select(
            IntegrationDeliveryMetricCohort.metric_id.label("metric_id"),
            func.sum(IntegrationDeliveryMetricCohort.succeeded_count).label(
                "succeeded_count"
            ),
            func.sum(IntegrationDeliveryMetricCohort.failed_count).label(
                "failed_count"
            ),
            func.sum(IntegrationDeliveryMetricCohort.dead_letter_count).label(
                "dead_letter_count"
            ),
            func.sum(IntegrationDeliveryMetricCohort.attempt_count).label(
                "attempt_count"
            ),
            func.sum(IntegrationDeliveryMetricCohort.duration_total_ms).label(
                "duration_total_ms"
            ),
            func.max(IntegrationDeliveryMetricCohort.duration_max_ms).label(
                "duration_max_ms"
            ),
        )
        .where(IntegrationDeliveryMetricCohort.metric_id.in_(metric_ids))
        .group_by(IntegrationDeliveryMetricCohort.metric_id)
        .subquery()
    )
    mismatches = int(
        db.scalar(
            select(func.count())
            .select_from(IntegrationDeliveryMetric)
            .outerjoin(
                totals,
                totals.c.metric_id == IntegrationDeliveryMetric.id,
            )
            .where(
                IntegrationDeliveryMetric.id.in_(metric_ids),
                or_(
                    IntegrationDeliveryMetric.succeeded_count
                    != func.coalesce(totals.c.succeeded_count, 0),
                    IntegrationDeliveryMetric.failed_count
                    != func.coalesce(totals.c.failed_count, 0),
                    IntegrationDeliveryMetric.dead_letter_count
                    != func.coalesce(totals.c.dead_letter_count, 0),
                    IntegrationDeliveryMetric.attempt_count
                    != func.coalesce(totals.c.attempt_count, 0),
                    IntegrationDeliveryMetric.duration_total_ms
                    != func.coalesce(totals.c.duration_total_ms, 0),
                    IntegrationDeliveryMetric.duration_max_ms
                    != func.coalesce(totals.c.duration_max_ms, 0),
                ),
            )
        )
        or 0
    )
    if mismatches:
        raise RuntimeError(
            "Integration delivery metric cohort totals do not match their base metrics."
        )


def _metric_key_sort(
    key: tuple[datetime, uuid.UUID, str, str],
) -> tuple[datetime, str, str, str]:
    bucket_start, integration_id, connector_type, event_type = key
    return bucket_start, str(integration_id), connector_type, event_type


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
