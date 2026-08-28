import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, JSON, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


SYSTEM_OPERATION_TYPES = (
    "backup",
    "verify",
    "restore_drill",
    "restore",
    "diagnostics",
)
SYSTEM_OPERATION_STATUSES = ("running", "succeeded", "failed")


class SystemOperationRun(Base):
    __tablename__ = "system_operation_runs"
    __table_args__ = (
        CheckConstraint(
            "operation_type IN ('backup', 'verify', 'restore_drill', 'restore', 'diagnostics')",
            name="ck_system_operation_runs_operation_type",
        ),
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')",
            name="ck_system_operation_runs_status",
        ),
        Index("ix_system_operation_runs_started_at", "started_at"),
        Index("ix_system_operation_runs_type_started", "operation_type", "started_at"),
        Index("ix_system_operation_runs_status_started", "status", "started_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running", server_default="running")
    initiated_by: Mapped[str] = mapped_column(String(255), nullable=False, default="system", server_default="system")
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="offline", server_default="offline")
    metadata_json: Mapped[dict] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
        default=dict,
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
