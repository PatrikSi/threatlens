import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, JSON, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ReportTemplate(Base):
    __tablename__ = "report_templates"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    builtin_key: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    report_type: Mapped[str] = mapped_column(String(64), nullable=False, default="custom", server_default="custom")
    visibility: Mapped[str] = mapped_column(String(16), nullable=False, default="private", server_default="private", index=True)
    audience: Mapped[str] = mapped_column(String(64), nullable=False, default="security_team", server_default="security_team")
    objective: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    tone: Mapped[str] = mapped_column(String(32), nullable=False, default="analytical", server_default="analytical")
    detail_level: Mapped[str] = mapped_column(String(16), nullable=False, default="standard", server_default="standard")
    use_company_context: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")
    custom_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    focus_topics_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    excluded_topics_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    sections_json: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    default_filters_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
