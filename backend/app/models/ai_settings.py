import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, JSON, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AISettings(Base):
    __tablename__ = "ai_settings"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider_type: Mapped[str] = mapped_column(String(32), nullable=False, default="openai_compatible", server_default="openai_compatible")
    base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    temperature: Mapped[float] = mapped_column(Float, nullable=False, default=0.2, server_default="0.2")
    max_completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=700, server_default="700")
    request_timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60, server_default="60")
    request_max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    summary_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    relevance_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    daily_brief_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    auto_enrich_new_items: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    daily_brief_window_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=24, server_default="24")
    daily_brief_max_items: Mapped[int] = mapped_column(Integer, nullable=False, default=20, server_default="20")
    daily_brief_history_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=7, server_default="7")
    relevance_medium_threshold: Mapped[float] = mapped_column(Float, nullable=False, default=0.55, server_default="0.55")
    relevance_high_threshold: Mapped[float] = mapped_column(Float, nullable=False, default=0.8, server_default="0.8")
    company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    company_industry: Mapped[str | None] = mapped_column(String(255), nullable=True)
    company_regions_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    company_stack_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    company_priority_topics_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    company_keywords_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    company_exclusions_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    company_profile_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    item_enrichment_system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    daily_brief_system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    global_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    item_summary_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    relevance_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    daily_brief_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
