import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AlertOccurrence(Base):
    __tablename__ = "alert_occurrences"
    __table_args__ = (
        UniqueConstraint(
            "rule_id_snapshot",
            "rule_revision",
            "item_id_snapshot",
            "item_content_hash",
            name="uq_alert_occurrences_rule_revision_item_content",
        ),
        Index(
            "ix_alert_occurrences_owner_state_created",
            "owner_user_id",
            "lifecycle_state",
            "created_at",
        ),
        Index(
            "ix_alert_occurrences_owner_severity_created",
            "owner_user_id",
            "severity_snapshot",
            "created_at",
        ),
        Index("ix_alert_occurrences_item_id", "item_id"),
        Index("ix_alert_occurrences_rule_id", "alert_interest_id"),
        Index(
            "ix_alert_occurrences_acknowledged_by_user_id", "acknowledged_by_user_id"
        ),
        Index(
            "ix_alert_occurrences_investigating_by_user_id", "investigating_by_user_id"
        ),
        Index("ix_alert_occurrences_closed_by_user_id", "closed_by_user_id"),
        Index(
            "ix_alert_occurrences_retention",
            "lifecycle_state",
            "closed_at",
            "metrics_aggregated_at",
        ),
        CheckConstraint(
            "lifecycle_state IN ('new', 'acknowledged', 'investigating', 'closed')",
            name="ck_alert_occurrences_lifecycle_state",
        ),
        CheckConstraint(
            "severity_snapshot IN ('low', 'medium', 'high', 'critical')",
            name="ck_alert_occurrences_severity",
        ),
        CheckConstraint(
            "rule_revision >= 1", name="ck_alert_occurrences_rule_revision"
        ),
        CheckConstraint("version >= 1", name="ck_alert_occurrences_version"),
        CheckConstraint(
            "(lifecycle_state = 'closed' AND closure_disposition IS NOT NULL) OR "
            "(lifecycle_state <> 'closed' AND closure_disposition IS NULL)",
            name="ck_alert_occurrences_closed_disposition",
        ),
        CheckConstraint(
            "(suppressed_at IS NULL AND suppression_reason IS NULL) OR "
            "(suppressed_at IS NOT NULL AND suppression_reason IS NOT NULL)",
            name="ck_alert_occurrences_suppression_pair",
        ),
        CheckConstraint(
            "(snoozed_until IS NULL AND snooze_reason IS NULL) OR "
            "(snoozed_until IS NOT NULL AND snooze_reason IS NOT NULL)",
            name="ck_alert_occurrences_snooze_pair",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    alert_interest_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("alert_interests.id", ondelete="SET NULL"),
        nullable=True,
    )
    rule_id_snapshot: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False
    )
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    item_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("items.id", ondelete="SET NULL"),
        nullable=True,
    )
    item_id_snapshot: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False
    )
    integration_event_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("integration_events.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    rule_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    item_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    alert_name_snapshot: Mapped[str] = mapped_column(String(255), nullable=False)
    alert_category_snapshot: Mapped[str] = mapped_column(String(64), nullable=False)
    alert_keywords_snapshot: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    matched_keywords: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    source_snapshot_json: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict
    )
    severity_snapshot: Mapped[str] = mapped_column(String(16), nullable=False)
    lifecycle_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="new", server_default="new"
    )
    suppressed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    suppression_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    snoozed_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    snooze_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    closure_disposition: Mapped[str | None] = mapped_column(String(64), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    acknowledged_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    investigating_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    investigating_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    metrics_aggregated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    @property
    def is_suppressed(self) -> bool:
        return self.suppressed_at is not None

    @property
    def is_snoozed(self) -> bool:
        if self.snoozed_until is None:
            return False
        value = self.snoozed_until
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value > datetime.now(timezone.utc)


class AlertOccurrenceActivity(Base):
    __tablename__ = "alert_occurrence_activities"
    __table_args__ = (
        Index(
            "ix_alert_occurrence_activities_occurrence_created",
            "occurrence_id",
            "created_at",
        ),
        Index("ix_alert_occurrence_activities_actor_user_id", "actor_user_id"),
        Index("ix_alert_occurrence_activities_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    occurrence_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("alert_occurrences.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    details_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AlertOccurrenceMetric(Base):
    __tablename__ = "alert_occurrence_metrics"
    __table_args__ = (
        UniqueConstraint(
            "bucket_start",
            "owner_user_id",
            "severity",
            "lifecycle_state",
            "suppressed",
            name="uq_alert_occurrence_metrics_bucket_dimensions",
        ),
        Index(
            "ix_alert_occurrence_metrics_owner_bucket", "owner_user_id", "bucket_start"
        ),
        Index("ix_alert_occurrence_metrics_bucket_start", "bucket_start"),
        CheckConstraint(
            "severity IN ('low', 'medium', 'high', 'critical')",
            name="ck_alert_occurrence_metrics_severity",
        ),
        CheckConstraint(
            "lifecycle_state IN ('new', 'acknowledged', 'investigating', 'closed')",
            name="ck_alert_occurrence_metrics_lifecycle_state",
        ),
        CheckConstraint(
            "occurrence_count >= 0", name="ck_alert_occurrence_metrics_count"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    bucket_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    lifecycle_state: Mapped[str] = mapped_column(String(16), nullable=False)
    suppressed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    occurrence_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
