"""Durable progress for expired parents whose child rows exceed one batch."""
from datetime import datetime
import uuid

from sqlalchemy import BigInteger, CheckConstraint, DateTime, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LifecyclePruningRecord(Base):
    __tablename__ = "lifecycle_pruning_records"
    __table_args__ = (
        CheckConstraint("children_pruned >= 0", name="ck_lifecycle_pruning_nonnegative"),
    )

    dataset: Mapped[str] = mapped_column(String(64), primary_key=True)
    parent_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    children_pruned: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0", default=0)
