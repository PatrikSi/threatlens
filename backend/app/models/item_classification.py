import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ItemClassification(Base):
    __tablename__ = "item_classifications"

    __table_args__ = (
        Index("ix_item_classifications_primary_category", "primary_category"),
        Index("ix_item_classifications_classified_at", "classified_at"),
        Index("ix_item_classifications_source_hash", "source_hash"),
    )

    item_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    primary_category: Mapped[str] = mapped_column(String(64), nullable=False)
    secondary_categories: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    scores_json: Mapped[dict[str, float]] = mapped_column(JSON, nullable=False, default=dict)
    matched_terms_json: Mapped[dict[str, list[str]]] = mapped_column(JSON, nullable=False, default=dict)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    rules_version: Mapped[str] = mapped_column(String(32), nullable=False, default="v1", server_default="v1")
    classified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
