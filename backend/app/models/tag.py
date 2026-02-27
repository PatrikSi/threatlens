import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, PrimaryKeyConstraint, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

TAG_SOURCES = ("rule", "ioc", "manual", "ml")


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)


class ItemTag(Base):
    __tablename__ = "item_tags"
    __table_args__ = (
        PrimaryKeyConstraint("item_id", "tag_id", name="pk_item_tags"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_item_tags_confidence"),
        CheckConstraint(
            "source IN ('rule', 'ioc', 'manual', 'ml')",
            name="ck_item_tags_source",
        ),
        Index("ix_item_tags_item_source", "item_id", "source"),
    )

    item_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("items.id", ondelete="CASCADE"), nullable=False)
    tag_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("tags.id", ondelete="CASCADE"), nullable=False)
    confidence: Mapped[float] = mapped_column(nullable=False, default=0.5, server_default="0.5")
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="rule", server_default="rule")
    rules_version: Mapped[str | None] = mapped_column(String(64), nullable=True, default="legacy", server_default="legacy")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
