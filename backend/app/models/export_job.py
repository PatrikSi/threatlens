import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ExportJob(Base):
    __tablename__ = "export_jobs"
    __table_args__ = (
        UniqueConstraint("principal_type", "principal_id", "idempotency_key", name="uq_export_jobs_request"),
        CheckConstraint("principal_type IN ('user', 'service_account')", name="ck_export_jobs_principal"),
        CheckConstraint("status IN ('queued', 'running', 'ready', 'failed', 'cancelled', 'expired')", name="ck_export_jobs_status"),
        CheckConstraint("attempts >= 0 AND reserved_bytes >= 0 AND completed_items >= 0", name="ck_export_jobs_bounds"),
        Index("ix_export_jobs_owner_created", "principal_type", "principal_id", "created_at", "id"),
        Index("ix_export_jobs_dispatch", "status", "next_attempt_at", "lease_expires_at"),
        Index("ix_export_jobs_expiry", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    principal_type: Mapped[str] = mapped_column(String(24), nullable=False)
    principal_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    idempotency_key: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_encrypted: Mapped[dict] = mapped_column(JSON, nullable=False, deferred=True)
    authorization_encrypted: Mapped[dict] = mapped_column(JSON, nullable=False)
    format: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    claim_token: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    next_dispatch_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_canary_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    reserved_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    completed_items: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    item_count: Mapped[int | None] = mapped_column(Integer)
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    filename: Mapped[str | None] = mapped_column(String(255))
    media_type: Mapped[str | None] = mapped_column(String(120))
    source_encrypted: Mapped[dict | None] = mapped_column(JSON, deferred=True)
    error_code: Mapped[str | None] = mapped_column(String(64))


class ExportJobChunk(Base):
    __tablename__ = "export_job_chunks"
    __table_args__ = (
        CheckConstraint("position >= 0 AND length(ciphertext) <= 500000", name="ck_export_job_chunks_bounds"),
    )
    job_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("export_jobs.id", ondelete="CASCADE"), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
