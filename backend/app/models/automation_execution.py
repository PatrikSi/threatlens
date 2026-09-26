"""Durable receiver receipts; HTTP delivery never implies external execution."""

from datetime import datetime
import uuid

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AutomationExecution(Base):
    __tablename__ = "automation_executions"
    __table_args__ = (
        UniqueConstraint(
            "webhook_id", "action_id", name="uq_automation_execution_action"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # Destination identity is retained when configuration is deleted; receivers
    # can still acknowledge withdrawals and completed hunt history survives.
    webhook_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("integration_events.id", ondelete="RESTRICT")
    )
    action_id: Mapped[str] = mapped_column(String(128))
    external_job_id: Mapped[str | None] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(
        String(16), default="unknown", server_default="unknown"
    )
    sequence: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    progress_stage: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    findings: Mapped[str | None] = mapped_column(Text)
    investigation_note_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("investigation_notes.id", ondelete="SET NULL")
    )
    policy_revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    policy_acknowledged_revision: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    policy_state: Mapped[str] = mapped_column(
        String(16), default="current", server_default="current"
    )
    next_check_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AutomationCallback(Base):
    __tablename__ = "automation_callbacks"
    __table_args__ = (
        UniqueConstraint(
            "execution_id", "sequence", name="uq_automation_callback_sequence"
        ),
    )
    execution_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("automation_executions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    callback_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer)
    digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AutomationPolicyUpdate(Base):
    __tablename__ = "automation_policy_updates"
    __table_args__ = (
        UniqueConstraint(
            "execution_id", "revision", name="uq_automation_policy_revision"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    execution_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("automation_executions.id", ondelete="CASCADE"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(String(256))
    replacement_action_id: Mapped[str | None] = mapped_column(String(128))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
