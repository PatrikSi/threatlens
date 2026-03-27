import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AIDailyBriefSourceItem(Base):
    __tablename__ = "ai_daily_brief_source_items"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    daily_brief_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("ai_daily_briefs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    item_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("items.id", ondelete="SET NULL"), nullable=True, index=True
    )
    included: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    exclusion_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    title_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    feed_name_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    url_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    classification_snapshot: Mapped[str | None] = mapped_column(String(64), nullable=True)
    relevance_score_snapshot: Mapped[float | None] = mapped_column(Float, nullable=True)
    relevance_label_snapshot: Mapped[str | None] = mapped_column(String(16), nullable=True)
    published_at_snapshot: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_seen_at_snapshot: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
