import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, PrimaryKeyConstraint, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class IOC(Base):
    __tablename__ = "iocs"

    __table_args__ = (
        UniqueConstraint("type", "value_norm", name="uq_iocs_type_value_norm"),
        Index("ix_iocs_type", "type"),
        Index("ix_iocs_value_norm", "value_norm"),
        Index("ix_iocs_last_seen_at", "last_seen_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    value_raw: Mapped[str] = mapped_column(Text, nullable=False)
    value_norm: Mapped[str] = mapped_column(Text, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ItemIOC(Base):
    __tablename__ = "item_iocs"

    __table_args__ = (
        PrimaryKeyConstraint("item_id", "ioc_id", name="pk_item_iocs"),
        Index("ix_item_iocs_ioc_id", "ioc_id"),
    )

    item_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("items.id", ondelete="CASCADE"), nullable=False)
    ioc_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("iocs.id", ondelete="CASCADE"), nullable=False)
    source_section: Mapped[str] = mapped_column(String(32), nullable=False, default="article", server_default="article")
    occurrences: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0, server_default="1")
