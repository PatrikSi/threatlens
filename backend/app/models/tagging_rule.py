import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TaggingRule(Base):
    __tablename__ = "tagging_rules"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    tag_name: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    match_type: Mapped[str] = mapped_column(String(16), nullable=False, default="contains", server_default="contains")
    pattern: Mapped[str] = mapped_column(Text, nullable=False)
    case_sensitive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    applies_to_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    required_categories_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    feed_scope: Mapped[str] = mapped_column(String(16), nullable=False, default="all", server_default="all")
    feed_ids_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    min_classification_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
