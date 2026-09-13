"""Durable dispatch ownership and bounded, principal-owned processing recovery."""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
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


class ProcessingRecoveryRun(Base):
    __tablename__ = "processing_recovery_runs"
    __table_args__ = (
        UniqueConstraint(
            "principal_type",
            "principal_id",
            "idempotency_key",
            name="uq_processing_recovery_request",
        ),
        CheckConstraint(
            "status IN ('queued','running','succeeded','partial','cancelled','failed')",
            name="ck_processing_recovery_status",
        ),
        CheckConstraint(
            "version > 0 AND total_count BETWEEN 1 AND 100",
            name="ck_processing_recovery_bounds",
        ),
        Index(
            "ix_processing_recovery_owner",
            "principal_type",
            "principal_id",
            "created_at",
            "id",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    principal_type: Mapped[str] = mapped_column(String(24), nullable=False)
    principal_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    idempotency_key: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    authorization_encrypted: Mapped[dict] = mapped_column(JSON, nullable=False)
    source_encrypted: Mapped[dict] = mapped_column(JSON, nullable=False, deferred=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    total_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class ProcessingWork(Base):
    __tablename__ = "processing_work"
    __table_args__ = (
        UniqueConstraint("item_id", "stage", name="uq_processing_work_item_stage"),
        CheckConstraint(
            "stage IN ('article','classification','ioc','tagging')",
            name="ck_processing_work_stage",
        ),
        CheckConstraint(
            "status IN ('waiting','queued','running','retry_wait','succeeded','attention','cancelled')",
            name="ck_processing_work_status",
        ),
        CheckConstraint(
            "generation > 0 AND version > 0 AND attempts >= 0",
            name="ck_processing_work_bounds",
        ),
        Index(
            "ix_processing_work_dispatch", "status", "next_retry_at", "lease_expires_at"
        ),
        Index("ix_processing_work_feed", "feed_id", "status"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    item_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("items.id", ondelete="CASCADE"), nullable=False
    )
    feed_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("feeds.id", ondelete="CASCADE"), nullable=False
    )
    stage: Mapped[str] = mapped_column(String(24), nullable=False)
    source_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    required_since_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    claim_token: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_canary_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(String(64))
    recovery_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("processing_recovery_runs.id", ondelete="SET NULL")
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


class ProcessingRecoveryItem(Base):
    __tablename__ = "processing_recovery_items"
    __table_args__ = (
        CheckConstraint(
            "state IN ('queued','running','succeeded','failed','cancelled')",
            name="ck_processing_recovery_item_state",
        ),
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("processing_recovery_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    item_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    stage: Mapped[str] = mapped_column(String(24), primary_key=True)
    work_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("processing_work.id", ondelete="SET NULL")
    )
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    reason: Mapped[str | None] = mapped_column(String(64))


class ProcessingDispatchState(Base):
    """Single durable round-robin cursor, independent of mutable Feed row locks."""

    __tablename__ = "processing_dispatch_state"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_processing_dispatch_singleton"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_feed_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
