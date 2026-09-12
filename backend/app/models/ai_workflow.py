"""Durable AI queue publications and immutable article batch membership."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AIWorkflowDispatch(Base):
    __tablename__ = "ai_workflow_dispatches"
    __table_args__ = (Index("ix_ai_workflow_dispatch_due", "state", "next_attempt_at"),)

    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("ai_task_runs.id", ondelete="CASCADE"), primary_key=True
    )
    task_name: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    delivery_id: Mapped[str | None] = mapped_column(String(255))
    claim_token: Mapped[str | None] = mapped_column(String(64))
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(String(128))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class AIReprocessMember(Base):
    __tablename__ = "ai_reprocess_members"
    __table_args__ = (Index("ix_ai_reprocess_members_child", "child_run_id", unique=True),)

    parent_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("ai_task_runs.id", ondelete="CASCADE"), primary_key=True
    )
    # Retain the accepted identity even when the article is subsequently deleted.
    item_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    child_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    outcome: Mapped[str | None] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(String(64))
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AIReportStageArtifact(Base):
    __tablename__ = "ai_report_stage_artifacts"

    task_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("ai_task_runs.id", ondelete="CASCADE"), primary_key=True
    )
    operation_scope: Mapped[str] = mapped_column(String(128), primary_key=True)
    report_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("reports.id", ondelete="CASCADE"), nullable=False, index=True
    )
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    completion_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
