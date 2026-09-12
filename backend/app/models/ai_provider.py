"""Named AI connections and routing, independent of legacy global settings."""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.ai_provider_capabilities import AIProviderCapabilities


class AIProviderConfiguration(AIProviderCapabilities, Base):
    __tablename__ = "ai_provider_configurations"
    __table_args__ = (CheckConstraint("version >= 1", name="ck_ai_provider_version"),)

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    normalized_name: Mapped[str] = mapped_column(
        String(360), nullable=False, unique=True
    )
    provider_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="openai_compatible",
        server_default="openai_compatible",
    )
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    temperature: Mapped[float | None] = mapped_column(
        Float().evaluates_none(), nullable=True, default=0.2, server_default="0.2"
    )
    max_completion_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5000, server_default="5000"
    )
    request_timeout_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=300, server_default="300"
    )
    request_max_retries: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default="3"
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    api_key_encrypted: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
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


class AIProviderRouting(Base):
    __tablename__ = "ai_provider_routing"
    __table_args__ = (
        CheckConstraint("singleton_key = 1", name="ck_ai_provider_routing_singleton"),
        CheckConstraint("version >= 1", name="ck_ai_provider_routing_version"),
    )

    singleton_key: Mapped[int] = mapped_column(
        Integer, primary_key=True, default=1, server_default="1"
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    default_provider_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("ai_provider_configurations.id", ondelete="RESTRICT"),
    )
    item_enrichment_provider_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("ai_provider_configurations.id", ondelete="RESTRICT"),
    )
    daily_brief_provider_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("ai_provider_configurations.id", ondelete="RESTRICT"),
    )
    report_provider_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("ai_provider_configurations.id", ondelete="RESTRICT"),
    )


class AIProviderRetiredID(Base):
    """Prevent deleted identifiers from retargeting queued or restored work."""

    __tablename__ = "ai_provider_retired_ids"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    retired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
