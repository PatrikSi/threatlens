import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


LIFECYCLE_TARGET_KEYS = (
    "article_content",
    "audit_logs",
    "action_approval_history",
    "ai_task_history",
    "ai_usage_history",
    "tag_feedback_history",
    "integration_run_history",
    "inactive_auth_sessions",
    "system_health_samples",
    "integration_delivery_history",
    "integration_event_history",
    "integration_metrics",
    "closed_alert_history",
    "alert_activity_history",
    "alert_evaluation_history",
    "alert_metrics",
)
LIFECYCLE_SCHEDULE_CADENCES = ("daily", "weekly")
LIFECYCLE_RUN_TRIGGERS = ("manual", "scheduled")
LIFECYCLE_RUN_STATUSES = (
    "queued",
    "running",
    "succeeded",
    "partial",
    "failed",
    "cancelled",
)
LIFECYCLE_ACTIVE_RUN_STATUSES = ("queued", "running")

_JSON = JSON().with_variant(JSONB(), "postgresql")
_TARGETS_SQL = ", ".join(f"'{target}'" for target in LIFECYCLE_TARGET_KEYS)


class LifecycleCatalogState(Base):
    __tablename__ = "lifecycle_catalog_state"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_lifecycle_catalog_state_singleton"),
        CheckConstraint(
            "catalog_version = 1",
            name="ck_lifecycle_catalog_state_version",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    catalog_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    bootstrapped_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    bootstrap_snapshot_json: Mapped[dict] = mapped_column(
        _JSON,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class LifecyclePolicy(Base):
    __tablename__ = "lifecycle_policies"
    __table_args__ = (
        CheckConstraint(
            f"target_key IN ({_TARGETS_SQL})",
            name="ck_lifecycle_policies_target_key",
        ),
        CheckConstraint(
            "retention_days BETWEEN 1 AND 3650",
            name="ck_lifecycle_policies_retention_days",
        ),
        CheckConstraint(
            "schedule_cadence IN ('daily', 'weekly')",
            name="ck_lifecycle_policies_schedule_cadence",
        ),
        CheckConstraint(
            "schedule_hour_utc BETWEEN 0 AND 23",
            name="ck_lifecycle_policies_schedule_hour_utc",
        ),
        CheckConstraint(
            "schedule_weekday IS NULL OR schedule_weekday BETWEEN 0 AND 6",
            name="ck_lifecycle_policies_schedule_weekday",
        ),
        CheckConstraint(
            "(schedule_cadence = 'weekly' AND schedule_weekday IS NOT NULL) "
            "OR (schedule_cadence = 'daily' AND schedule_weekday IS NULL)",
            name="ck_lifecycle_policies_schedule_shape",
        ),
        CheckConstraint(
            "max_records_per_run BETWEEN 100 AND 100000",
            name="ck_lifecycle_policies_max_records_per_run",
        ),
        CheckConstraint(
            "revision >= 1",
            name="ck_lifecycle_policies_revision",
        ),
        CheckConstraint(
            "last_run_status IS NULL OR last_run_status IN "
            "('queued', 'running', 'succeeded', 'partial', 'failed', 'cancelled')",
            name="ck_lifecycle_policies_last_run_status",
        ),
        CheckConstraint(
            "(enabled AND next_run_at IS NOT NULL) OR "
            "(NOT enabled AND next_run_at IS NULL)",
            name="ck_lifecycle_policies_schedule_activation",
        ),
        CheckConstraint(
            "updated_by_user_id IS NULL OR "
            "(updated_by_label_snapshot IS NOT NULL AND "
            "length(trim(updated_by_label_snapshot)) BETWEEN 1 AND 320)",
            name="ck_lifecycle_policies_actor_snapshot",
        ),
        Index("ix_lifecycle_policies_due", "enabled", "next_run_at"),
    )

    target_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    retention_days: Mapped[int] = mapped_column(Integer, nullable=False)
    schedule_cadence: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="daily",
        server_default="daily",
    )
    schedule_hour_utc: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=2,
        server_default="2",
    )
    schedule_weekday: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_records_per_run: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=10_000,
        server_default="10000",
    )
    options_json: Mapped[dict] = mapped_column(
        _JSON,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_run_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_by_label_snapshot: Mapped[str | None] = mapped_column(
        String(320),
        nullable=True,
    )
    configuration_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class LifecyclePreview(Base):
    __tablename__ = "lifecycle_previews"
    __table_args__ = (
        CheckConstraint(
            "policy_revision >= 1",
            name="ck_lifecycle_previews_policy_revision",
        ),
        CheckConstraint(
            "eligible_count >= 0 AND protected_count >= 0 "
            "AND (eligible_bytes IS NULL OR eligible_bytes >= 0)",
            name="ck_lifecycle_previews_nonnegative_counts",
        ),
        CheckConstraint(
            "expires_at > generated_at",
            name="ck_lifecycle_previews_expiry",
        ),
        CheckConstraint(
            "used_at IS NULL OR "
            "(used_at >= generated_at AND used_at <= expires_at)",
            name="ck_lifecycle_previews_consumption_time",
        ),
        Index("ix_lifecycle_previews_target_generated", "target_key", "generated_at"),
        Index("ix_lifecycle_previews_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    target_key: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("lifecycle_policies.target_key", ondelete="CASCADE"),
        nullable=False,
    )
    policy_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_snapshot_json: Mapped[dict] = mapped_column(
        _JSON,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    eligible_count: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default="0",
    )
    protected_count: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default="0",
    )
    protected_counts_json: Mapped[dict] = mapped_column(
        _JSON,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    oldest_candidate_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    count_is_lower_bound: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    is_partial: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    eligible_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class LifecycleRun(Base):
    __tablename__ = "lifecycle_runs"
    __table_args__ = (
        CheckConstraint(
            "trigger_source IN ('manual', 'scheduled')",
            name="ck_lifecycle_runs_trigger_source",
        ),
        CheckConstraint(
            "status IN "
            "('queued', 'running', 'succeeded', 'partial', 'failed', 'cancelled')",
            name="ck_lifecycle_runs_status",
        ),
        CheckConstraint(
            "policy_revision >= 1",
            name="ck_lifecycle_runs_policy_revision",
        ),
        CheckConstraint(
            "max_records BETWEEN 100 AND 100000",
            name="ck_lifecycle_runs_max_records",
        ),
        CheckConstraint(
            "evaluated_count >= 0 AND affected_count >= 0 "
            "AND protected_count >= 0 AND skipped_count >= 0 "
            "AND batch_count >= 0 "
            "AND (remaining_count IS NULL OR remaining_count >= 0) "
            "AND (affected_bytes IS NULL OR affected_bytes >= 0)",
            name="ck_lifecycle_runs_nonnegative_counts",
        ),
        CheckConstraint(
            "affected_count <= max_records",
            name="ck_lifecycle_runs_affected_within_limit",
        ),
        CheckConstraint(
            "(status IN ('queued', 'running') AND finished_at IS NULL) "
            "OR (status IN ('succeeded', 'partial', 'failed', 'cancelled') "
            "AND finished_at IS NOT NULL)",
            name="ck_lifecycle_runs_terminal_timestamp_shape",
        ),
        CheckConstraint(
            "(trigger_source = 'scheduled' AND scheduled_for IS NOT NULL) "
            "OR (trigger_source = 'manual' AND scheduled_for IS NULL)",
            name="ck_lifecycle_runs_schedule_shape",
        ),
        CheckConstraint(
            "(trigger_source = 'scheduled' AND preview_id IS NULL "
            "AND preview_id_snapshot IS NULL AND requested_by_user_id IS NULL "
            "AND requested_by_user_id_snapshot IS NULL "
            "AND requested_by_label_snapshot IS NULL "
            "AND idempotency_key_hash IS NULL AND request_fingerprint IS NULL) "
            "OR (trigger_source = 'manual' AND preview_id_snapshot IS NOT NULL "
            "AND requested_by_user_id_snapshot IS NOT NULL "
            "AND requested_by_label_snapshot IS NOT NULL "
            "AND length(trim(requested_by_label_snapshot)) BETWEEN 1 AND 320 "
            "AND reason IS NOT NULL AND length(trim(reason)) BETWEEN 10 AND 500 "
            "AND idempotency_key_hash IS NOT NULL "
            "AND length(idempotency_key_hash) = 64 "
            "AND request_fingerprint IS NOT NULL "
            "AND length(request_fingerprint) = 64)",
            name="ck_lifecycle_runs_actor_evidence",
        ),
        CheckConstraint(
            "(NOT cancel_requested AND cancel_requested_at IS NULL "
            "AND cancel_requested_by_user_id IS NULL "
            "AND cancel_requested_by_principal_type IS NULL "
            "AND cancel_requested_by_user_id_snapshot IS NULL "
            "AND cancel_requested_by_label_snapshot IS NULL "
            "AND cancellation_reason IS NULL) OR "
            "(cancel_requested AND cancel_requested_at IS NOT NULL "
            "AND cancellation_reason IS NOT NULL "
            "AND length(trim(cancellation_reason)) BETWEEN 10 AND 500 "
            "AND ((cancel_requested_by_principal_type = 'user' "
            "AND cancel_requested_by_user_id_snapshot IS NOT NULL "
            "AND cancel_requested_by_label_snapshot IS NOT NULL "
            "AND length(trim(cancel_requested_by_label_snapshot)) BETWEEN 1 AND 320) "
            "OR (cancel_requested_by_principal_type = 'system' "
            "AND cancel_requested_by_user_id IS NULL "
            "AND cancel_requested_by_user_id_snapshot IS NULL "
            "AND cancel_requested_by_label_snapshot IS NULL)))",
            name="ck_lifecycle_runs_cancellation_shape",
        ),
        CheckConstraint(
            "NOT cancel_requested OR status IN ('queued', 'running', 'cancelled')",
            name="ck_lifecycle_runs_cancellation_status",
        ),
        CheckConstraint(
            "(status = 'running' AND started_at IS NOT NULL "
            "AND heartbeat_at IS NOT NULL AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL) OR "
            "(status <> 'running' AND lease_token IS NULL "
            "AND lease_expires_at IS NULL)",
            name="ck_lifecycle_runs_lease_shape",
        ),
        CheckConstraint(
            "cutoff_at <= queued_at",
            name="ck_lifecycle_runs_cutoff_before_queue",
        ),
        CheckConstraint(
            "(status IN ('queued', 'running') AND stop_reason IS NULL) OR "
            "(status IN ('succeeded', 'partial', 'failed', 'cancelled') "
            "AND stop_reason IS NOT NULL)",
            name="ck_lifecycle_runs_stop_reason_shape",
        ),
        UniqueConstraint(
            "requested_by_user_id",
            "target_key",
            "idempotency_key_hash",
            name="uq_lifecycle_runs_actor_target_idempotency",
        ),
        UniqueConstraint(
            "target_key",
            "scheduled_for",
            name="uq_lifecycle_runs_target_scheduled_for",
        ),
        Index(
            "uq_lifecycle_runs_active_target",
            "target_key",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
            sqlite_where=text("status IN ('queued', 'running')"),
        ),
        Index("ix_lifecycle_runs_target_created", "target_key", "created_at"),
        Index("ix_lifecycle_runs_status_created", "status", "created_at"),
        Index("ix_lifecycle_runs_trigger_created", "trigger_source", "created_at"),
        Index("ix_lifecycle_runs_created_at", "created_at"),
        Index("ix_lifecycle_runs_active_lease", "status", "lease_expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    target_key: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("lifecycle_policies.target_key", ondelete="RESTRICT"),
        nullable=False,
    )
    trigger_source: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="queued",
        server_default="queued",
    )
    policy_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_snapshot_json: Mapped[dict] = mapped_column(
        _JSON,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    preview_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("lifecycle_previews.id", ondelete="SET NULL"),
        nullable=True,
    )
    preview_id_snapshot: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
    )
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    requested_by_user_id_snapshot: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
    )
    requested_by_label_snapshot: Mapped[str | None] = mapped_column(
        String(320),
        nullable=True,
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scheduled_for: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    max_records: Mapped[int] = mapped_column(Integer, nullable=False)
    evaluated_count: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default="0",
    )
    affected_count: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default="0",
    )
    protected_count: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default="0",
    )
    skipped_count: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default="0",
    )
    batch_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    remaining_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    affected_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    details_json: Mapped[dict] = mapped_column(
        _JSON,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    stop_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    cancel_requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    cancel_requested_by_principal_type: Mapped[str | None] = mapped_column(
        String(16),
        nullable=True,
    )
    cancel_requested_by_user_id_snapshot: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
    )
    cancel_requested_by_label_snapshot: Mapped[str | None] = mapped_column(
        String(320),
        nullable=True,
    )
    cancellation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
