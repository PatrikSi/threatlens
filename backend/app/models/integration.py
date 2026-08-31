import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class IntegrationInstance(Base):
    __tablename__ = "integration_instances"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    credential_source_integration_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("integration_instances.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    system_key: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    integration_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    config_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    secret_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    health_status: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown", server_default="unknown")
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_test_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_concurrency: Mapped[int] = mapped_column(Integer, nullable=False, default=2, server_default="2")
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, nullable=False, default=60, server_default="60")
    circuit_state: Mapped[str] = mapped_column(String(16), nullable=False, default="closed", server_default="closed")
    circuit_failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    circuit_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    circuit_open_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class IntegrationRun(Base):
    __tablename__ = "integration_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    integration_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("integration_instances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    run_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class IntegrationSubscription(Base):
    __tablename__ = "integration_subscriptions"
    __table_args__ = (
        UniqueConstraint("integration_id", "subscription_key", name="uq_integration_subscriptions_instance_key"),
        Index("ix_integration_subscriptions_event_enabled", "event_type", "enabled"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    integration_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("integration_instances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    subscription_key: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    feed_scope: Mapped[str] = mapped_column(String(16), nullable=False, default="all", server_default="all", index=True)
    filter_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    transform_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class IntegrationSubscriptionFeed(Base):
    __tablename__ = "integration_subscription_feeds"

    subscription_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("integration_subscriptions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    feed_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("feeds.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )


class IntegrationEvent(Base):
    __tablename__ = "integration_events"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_integration_events_idempotency_key"),
        Index("ix_integration_events_routing_due", "routing_state", "available_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    routing_state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending")
    routing_attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    routed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class IntegrationDelivery(Base):
    __tablename__ = "integration_deliveries"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_integration_deliveries_idempotency_key"),
        Index("ix_integration_deliveries_state_due", "state", "not_before", "created_at"),
        Index("ix_integration_deliveries_instance_created", "integration_id", "created_at"),
        Index("ix_integration_deliveries_owner_created", "owner_user_id", "created_at"),
        Index(
            "uq_integration_deliveries_live_event_subscription",
            "event_id",
            "subscription_id",
            unique=True,
            postgresql_where=text("event_id IS NOT NULL AND delivery_kind = 'live'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    integration_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("integration_instances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("integration_subscriptions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    event_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("integration_events.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_delivery_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("integration_deliveries.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    connector_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    delivery_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="live", server_default="live")
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending", index=True)
    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3, server_default="3")
    not_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dead_lettered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metrics_aggregated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error_retryable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class IntegrationAttempt(Base):
    __tablename__ = "integration_attempts"
    __table_args__ = (
        UniqueConstraint("delivery_id", "attempt_number", name="uq_integration_attempts_delivery_number"),
        Index("ix_integration_attempts_instance_started", "integration_id", "started_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    delivery_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("integration_deliveries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    integration_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("integration_instances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retryable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    response_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class IntegrationDeliveryMetric(Base):
    __tablename__ = "integration_delivery_metrics"
    __table_args__ = (
        UniqueConstraint(
            "bucket_start",
            "integration_id",
            "connector_type",
            "event_type",
            name="uq_integration_delivery_metrics_bucket_dimension",
        ),
        Index("ix_integration_delivery_metrics_connector_bucket", "connector_type", "bucket_start"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    integration_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("integration_instances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    connector_type: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    succeeded_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    dead_letter_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    duration_total_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    duration_max_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class IntegrationDeliveryMetricCohort(Base):
    __tablename__ = "integration_delivery_metric_cohorts"
    __table_args__ = (
        UniqueConstraint(
            "metric_id",
            "policy_cohort_key",
            name="uq_integration_delivery_metric_cohorts_dimensions",
        ),
        Index(
            "ix_integration_delivery_metric_cohorts_metric",
            "metric_id",
        ),
        CheckConstraint(
            "policy_cohort_key ~ '^[0-9a-f]{64}$'",
            name="ck_integration_delivery_metric_cohorts_key",
        ),
        CheckConstraint(
            "captured_policy_revision >= 1",
            name="ck_integration_delivery_metric_cohorts_revision",
        ),
        CheckConstraint(
            "source_count >= 0 AND (source_count > 0 OR NOT provenance_complete)",
            name="ck_integration_delivery_metric_cohorts_provenance",
        ),
        CheckConstraint(
            "succeeded_count >= 0 AND failed_count >= 0 "
            "AND dead_letter_count >= 0 AND attempt_count >= 0 "
            "AND duration_total_ms >= 0 AND duration_max_ms >= 0",
            name="ck_integration_delivery_metric_cohorts_counters",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    metric_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("integration_delivery_metrics.id", ondelete="CASCADE"),
        nullable=False,
    )
    policy_cohort_key: Mapped[str] = mapped_column(String(64), nullable=False)
    captured_policy_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    provenance_complete: Mapped[bool] = mapped_column(Boolean, nullable=False)
    source_count: Mapped[int] = mapped_column(Integer, nullable=False)
    succeeded_count: Mapped[int] = mapped_column(Integer, nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False)
    dead_letter_count: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_total_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_max_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class IntegrationDeliveryMetricCohortLabel(Base):
    __tablename__ = "integration_delivery_metric_cohort_labels"
    __table_args__ = (
        Index(
            "ix_integration_delivery_metric_cohort_labels_label",
            "label_id",
            "cohort_id",
        ),
    )

    cohort_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("integration_delivery_metric_cohorts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    label_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("handling_labels.id", ondelete="RESTRICT"),
        primary_key=True,
    )


class IntegrationDeliveryMetricCohortCapturedLabel(Base):
    __tablename__ = "integration_delivery_metric_cohort_captured_labels"
    __table_args__ = (
        Index(
            "ix_integration_metric_captured_labels_label",
            "label_id",
            "cohort_id",
        ),
    )

    cohort_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("integration_delivery_metric_cohorts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    label_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("handling_labels.id", ondelete="RESTRICT"),
        primary_key=True,
    )


class IntegrationDeliveryMetricCohortTaintLabel(Base):
    __tablename__ = "integration_delivery_metric_cohort_taint_labels"
    __table_args__ = (
        Index(
            "ix_integration_metric_taint_labels_label",
            "label_id",
            "cohort_id",
        ),
    )

    cohort_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("integration_delivery_metric_cohorts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    label_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("handling_labels.id", ondelete="RESTRICT"),
        primary_key=True,
    )


class IntegrationDeliveryMetricCohortFeed(Base):
    __tablename__ = "integration_delivery_metric_cohort_feeds"
    __table_args__ = (
        Index(
            "ix_integration_delivery_metric_cohort_feeds_feed",
            "source_feed_id_snapshot",
            "cohort_id",
        ),
        CheckConstraint(
            "source_feed_id_snapshot <> "
            "'00000000-0000-0000-0000-000000000000'::uuid",
            name="ck_integration_delivery_metric_cohort_feeds_nonzero",
        ),
    )

    cohort_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("integration_delivery_metric_cohorts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    source_feed_id_snapshot: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
    )
