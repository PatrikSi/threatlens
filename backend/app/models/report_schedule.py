import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ReportSchedule(Base):
    __tablename__ = "report_schedules"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    template_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("report_templates.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true", index=True)
    cadence: Mapped[str] = mapped_column(String(16), nullable=False, default="weekly", server_default="weekly")
    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    day_of_month: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    hour: Mapped[int] = mapped_column(Integer, nullable=False, default=9, server_default="9")
    minute: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC", server_default="UTC")
    window_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="previous_complete_week", server_default="previous_complete_week"
    )
    rolling_days: Mapped[int] = mapped_column(Integer, nullable=False, default=7, server_default="7")
    filters_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    custom_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivery_enabled: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    delivery_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="summary", server_default="summary")
    skip_empty: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")
    missed_run_policy: Mapped[str] = mapped_column(String(16), nullable=False, default="latest", server_default="latest")
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="healthy", server_default="healthy", index=True
    )
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    consecutive_failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
