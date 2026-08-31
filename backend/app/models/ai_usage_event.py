import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AIUsageEvent(Base):
    __tablename__ = "ai_usage_events"
    __table_args__ = (
        Index("ix_ai_usage_events_item_id", "item_id"),
        Index("ix_ai_usage_events_daily_brief_id", "daily_brief_id"),
        Index("ix_ai_usage_events_report_id", "report_id"),
        Index("ix_ai_usage_events_task_run_snapshot", "task_run_id_snapshot"),
        CheckConstraint(
            "data_access_scope IN ('system', 'governed')",
            name="ck_ai_usage_events_data_access_scope",
        ),
        CheckConstraint(
            "data_access_scope <> 'system' OR "
            "(feature_type = 'connection_test' AND item_id IS NULL "
            "AND daily_brief_id IS NULL AND report_id IS NULL)",
            name="ck_ai_usage_events_system_scope",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    feature_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    item_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("items.id", ondelete="SET NULL"), nullable=True)
    daily_brief_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("ai_daily_briefs.id", ondelete="SET NULL"),
        nullable=True,
    )
    report_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("reports.id", ondelete="SET NULL"), nullable=True
    )
    task_run_id_snapshot: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )
    data_access_scope: Mapped[str] = mapped_column(
        String(16), nullable=False, default="governed", server_default="governed"
    )
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
