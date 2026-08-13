import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ReportSourceItem(Base):
    __tablename__ = "report_source_items"
    __table_args__ = (
        UniqueConstraint("report_id", "citation_key", name="uq_report_source_items_report_citation"),
        UniqueConstraint("report_id", "item_id", name="uq_report_source_items_report_item"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    report_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("reports.id", ondelete="CASCADE"), nullable=False, index=True
    )
    item_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("items.id", ondelete="SET NULL"), nullable=True, index=True
    )
    citation_key: Mapped[str] = mapped_column(String(16), nullable=False)
    included: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    exclusion_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    title_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    feed_name_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    url_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    classification_snapshot: Mapped[str | None] = mapped_column(String(64), nullable=True)
    relevance_score_snapshot: Mapped[float | None] = mapped_column(Float, nullable=True)
    relevance_label_snapshot: Mapped[str | None] = mapped_column(String(16), nullable=True)
    published_at_snapshot: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_seen_at_snapshot: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    tags_snapshot_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    iocs_snapshot_json: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    evidence_text: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    estimated_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
