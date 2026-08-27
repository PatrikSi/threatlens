import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AlertBackfillPreview(Base):
    __tablename__ = "alert_backfill_previews"
    __table_args__ = (
        Index(
            "ix_alert_backfill_previews_actor_expiry",
            "actor_user_id",
            "expires_at",
        ),
        Index("ix_alert_backfill_previews_expiry", "expires_at"),
        CheckConstraint(
            "item_limit >= 1 AND item_limit <= 500",
            name="ck_alert_backfill_previews_item_limit",
        ),
        CheckConstraint(
            "matched_count >= 0",
            name="ck_alert_backfill_previews_matched_count",
        ),
        CheckConstraint(
            "(cursor_first_seen_at IS NULL AND cursor_item_id IS NULL) OR "
            "(cursor_first_seen_at IS NOT NULL AND cursor_item_id IS NOT NULL)",
            name="ck_alert_backfill_previews_cursor_pair",
        ),
        CheckConstraint(
            "(next_cursor_first_seen_at IS NULL AND next_cursor_item_id IS NULL) OR "
            "(next_cursor_first_seen_at IS NOT NULL AND next_cursor_item_id IS NOT NULL)",
            name="ck_alert_backfill_previews_next_cursor_pair",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    since: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    item_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    cursor_first_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cursor_item_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )
    candidates_json: Mapped[list[dict]] = mapped_column(
        JSON, nullable=False, default=list
    )
    matched_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    has_more: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    next_cursor_first_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_cursor_item_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
