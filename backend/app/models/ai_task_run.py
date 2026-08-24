import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, JSON, String, Text, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AITaskRun(Base):
    __tablename__ = "ai_task_runs"
    __table_args__ = (
        Index(
            "ix_ai_task_runs_item_task_status_active",
            "item_id",
            "task_type",
            "status",
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
        Index(
            "uq_ai_task_runs_active_report",
            "report_id",
            unique=True,
            postgresql_where=text(
                "report_id IS NOT NULL AND task_type = 'report' "
                "AND status IN ('queued', 'running') AND finished_at IS NULL"
            ),
        ),
        Index(
            "uq_ai_task_runs_actor_request_idempotency_key",
            "actor_user_id",
            "request_idempotency_key_hash",
            unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    trigger_source: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    reason: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    worker_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    item_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("items.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    daily_brief_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("ai_daily_briefs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    report_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("reports.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("ai_task_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    superseded_by_task_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "ai_task_runs.id",
            name="fk_ai_task_runs_superseded_by_task_run_id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )
    model: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    prompt_char_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_char_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_text_chars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    dispatch_attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    dispatch_next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    dispatch_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    dispatch_claim_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dispatch_claim_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    dispatch_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dispatch_protocol_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=2,
        server_default="1",
    )
    request_idempotency_key_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    target_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    processed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    success_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    skipped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    skipped_unchanged_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    skipped_ineligible_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
