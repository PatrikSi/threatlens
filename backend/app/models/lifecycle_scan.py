"""Durable, bounded retention scan anchors; independent of policy revisions."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LifecycleScanCursor(Base):
    __tablename__ = "lifecycle_scan_cursors"
    __table_args__ = (
        CheckConstraint(
            "(last_timestamp IS NULL) = (last_id IS NULL)",
            name="ck_lifecycle_scan_cursor_anchor",
        ),
    )

    dataset: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
