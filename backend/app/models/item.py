import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Item(Base):
    __tablename__ = "items"

    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_items_dedupe_key"),
        Index("ix_items_feed_id", "feed_id"),
        Index("ix_items_source_guid", "source_guid"),
        Index("ix_items_canonical_url", "canonical_url"),
        Index("ix_items_published_at", "published_at"),
        Index("ix_items_first_seen_at", "first_seen_at"),
        Index("ix_items_content_hash", "content_hash"),
        Index(
            "ix_items_feed_guid_unique_not_null",
            "feed_id",
            "source_guid",
            unique=True,
            postgresql_where=text("source_guid IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    feed_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("feeds.id", ondelete="CASCADE"), nullable=False)
    source_guid: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    dedupe_key: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="new", server_default="new")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
