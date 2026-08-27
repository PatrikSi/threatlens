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
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AlertEvaluationRequest(Base):
    __tablename__ = "alert_evaluation_requests"
    __table_args__ = (
        UniqueConstraint(
            "item_id",
            "item_content_hash",
            name="uq_alert_evaluation_requests_item_content",
        ),
        Index(
            "ix_alert_evaluation_requests_recovery",
            "state",
            "available_at",
            "lease_expires_at",
        ),
        Index(
            "ix_alert_evaluation_requests_dispatch_claim",
            "dispatch_claimed_at",
        ),
        Index(
            "ix_alert_evaluation_requests_dispatch_failure",
            "state",
            "last_dispatch_failed_at",
        ),
        Index(
            "ix_alert_evaluation_requests_retention",
            "state",
            "completed_at",
        ),
        CheckConstraint(
            "state IN ('pending', 'processing', 'retry_wait', 'succeeded', 'dead_letter')",
            name="ck_alert_evaluation_requests_state",
        ),
        CheckConstraint(
            "attempt_count >= 0", name="ck_alert_evaluation_requests_attempt_count"
        ),
        CheckConstraint(
            "max_attempts >= 1", name="ck_alert_evaluation_requests_max_attempts"
        ),
        CheckConstraint(
            "source IN ('live', 'reconciliation', 'backfill')",
            name="ck_alert_evaluation_requests_source",
        ),
        CheckConstraint(
            "active_source IN ('live', 'reconciliation', 'backfill', 'replay')",
            name="ck_alert_evaluation_requests_active_source",
        ),
        CheckConstraint(
            "dispatch_attempt_count >= 0",
            name="ck_alert_evaluation_requests_dispatch_attempt_count",
        ),
        CheckConstraint(
            "dispatch_failure_count >= 0",
            name="ck_alert_evaluation_requests_dispatch_failure_count",
        ),
        CheckConstraint("version >= 1", name="ck_alert_evaluation_requests_version"),
        CheckConstraint(
            "accepted_rule_count >= 0 AND accepted_match_count >= 0",
            name="ck_alert_evaluation_requests_accepted_counts",
        ),
        CheckConstraint(
            "degraded_owner_count >= 0",
            name="ck_alert_evaluation_requests_degraded_owner_count",
        ),
        CheckConstraint(
            "backfill_count >= 0",
            name="ck_alert_evaluation_requests_backfill_count",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
        index=True,
    )
    item_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="live", server_default="live"
    )
    active_source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="live", server_default="live"
    )
    notify: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    notify_existing_occurrences: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    respect_rule_cutover: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5, server_default="5"
    )
    dispatch_attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    dispatch_failure_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    accepted_rule_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    accepted_match_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    degraded_owner_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    degraded_owners_json: Mapped[list[dict]] = mapped_column(
        JSON, nullable=False, default=list
    )
    backfill_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    dispatch_claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_dispatch_failed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lease_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_backfill_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_replayed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    evaluated_rule_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    occurrence_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class AlertEvaluationRequestActivity(Base):
    __tablename__ = "alert_evaluation_request_activities"
    __table_args__ = (
        Index(
            "ix_alert_evaluation_request_activities_request_created",
            "request_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_alert_evaluation_request_activities_actor_user_id",
            "actor_user_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    request_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("alert_evaluation_requests.id", ondelete="CASCADE"),
        nullable=False,
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    details_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
