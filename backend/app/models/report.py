import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("report_templates.id", ondelete="SET NULL"), nullable=True, index=True
    )
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("report_schedules.id", ondelete="SET NULL"), nullable=True, index=True
    )
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    report_type: Mapped[str] = mapped_column(String(64), nullable=False, default="custom", server_default="custom")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued", server_default="queued", index=True)
    trigger_source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual", server_default="manual")
    generation_stage: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", server_default="queued")
    generation_key: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    filters_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    prompt_config_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    generation_context_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    sections_config_json: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    metrics_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    coverage_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    summary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    included_source_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    excluded_source_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    citation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    estimated_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    context_window_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=8192, server_default="8192")
    model_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    generation_batches: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivery_requested: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    delivery_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="summary", server_default="summary")
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
