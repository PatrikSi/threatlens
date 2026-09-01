import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    JSON,
    String,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SystemHealthSample(Base):
    __tablename__ = "system_health_samples"
    __table_args__ = (
        CheckConstraint(
            "overall_status IN ('healthy', 'degraded', 'critical', 'unavailable', 'unknown')",
            name="ck_system_health_samples_overall_status",
        ),
        CheckConstraint(
            "worker_status IN ('healthy', 'degraded', 'critical', 'unavailable', 'unknown')",
            name="ck_system_health_samples_worker_status",
        ),
        CheckConstraint(
            "worker_reason IN ('healthy', 'no_replies', 'probe_failed', "
            "'queue_inventory_unavailable', 'partial_inventory', "
            "'missing_consumers', 'canary_dispatch_unavailable', "
            "'execution_evidence_missing', "
            "'execution_stalled', 'saturated')",
            name="ck_system_health_samples_worker_reason",
        ),
        CheckConstraint(
            "responding_worker_count >= 0 "
            "AND observed_worker_count >= responding_worker_count "
            "AND (total_capacity IS NULL OR total_capacity >= 0) "
            "AND (active_count IS NULL OR active_count >= 0) "
            "AND (reserved_count IS NULL OR reserved_count >= 0) "
            "AND (scheduled_count IS NULL OR scheduled_count >= 0) "
            "AND backlog_pending_count >= 0 AND backlog_stale_count >= 0 "
            "AND critical_issue_count >= 0 AND warning_issue_count >= 0",
            name="ck_system_health_samples_nonnegative_counts",
        ),
        Index("ix_system_health_samples_sampled_at", "sampled_at", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    sampled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    overall_status: Mapped[str] = mapped_column(String(16), nullable=False)
    component_statuses_json: Mapped[dict] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
        default=dict,
    )
    worker_status: Mapped[str] = mapped_column(String(16), nullable=False)
    worker_reason: Mapped[str] = mapped_column(String(64), nullable=False)
    responding_worker_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    observed_worker_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    worker_inventory_truncated: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    total_capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reserved_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scheduled_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    missing_queues_json: Mapped[list] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
        default=list,
    )
    stale_execution_queues_json: Mapped[list] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
        default=list,
    )
    backlog_pending_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    backlog_stale_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    critical_issue_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    warning_issue_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    issue_codes_json: Mapped[list] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
        default=list,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
