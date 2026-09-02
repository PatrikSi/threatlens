import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Article(Base):
    __tablename__ = "articles"
    __table_args__ = (
        UniqueConstraint("item_id", name="uq_articles_item_id"),
        CheckConstraint(
            "content_purge_run_id IS NULL OR content_purged_at IS NOT NULL",
            name="ck_articles_content_purge_reference",
        ),
        CheckConstraint(
            "content_purged_at IS NULL OR "
            "(text IS NULL AND title_extracted IS NULL AND language IS NULL "
            "AND word_count IS NULL AND extraction_method = 'retention_purged')",
            name="ck_articles_content_purge_shape",
        ),
        Index("ix_articles_item_id", "item_id", unique=True),
        Index(
            "ix_articles_retention_candidates",
            "item_id",
            "id",
            postgresql_where=text(
                "content_purged_at IS NULL AND (text IS NOT NULL OR "
                "title_extracted IS NOT NULL OR language IS NOT NULL OR "
                "word_count IS NOT NULL)"
            ),
            sqlite_where=text(
                "content_purged_at IS NULL AND (text IS NOT NULL OR "
                "title_extracted IS NOT NULL OR language IS NOT NULL OR "
                "word_count IS NOT NULL)"
            ),
        ),
        Index(
            "ix_articles_content_purge_run_id",
            "content_purge_run_id",
            postgresql_where=text("content_purge_run_id IS NOT NULL"),
            sqlite_where=text("content_purge_run_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("items.id", ondelete="CASCADE"), nullable=False
    )
    final_url: Mapped[str] = mapped_column(Text, nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    http_status: Mapped[int] = mapped_column(Integer, nullable=False)
    content_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    title_extracted: Mapped[str | None] = mapped_column(Text, nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_method: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[str | None] = mapped_column(Text, nullable=True)
    word_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fetch_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_purged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    content_purge_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("lifecycle_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
