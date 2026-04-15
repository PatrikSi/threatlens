import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class NotificationWebhookDelivery(Base):
    __tablename__ = "notification_webhook_deliveries"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    webhook_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("notification_webhooks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type_snapshot: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="rss_item_new",
        server_default="rss_item_new",
        index=True,
    )
    item_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("items.id", ondelete="SET NULL"), nullable=True)
    feed_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("feeds.id", ondelete="SET NULL"), nullable=True)
    source_delivery_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("notification_webhook_deliveries.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    scope_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    delivery_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="live", server_default="live")
    delivery_state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=10, server_default="10")
    rendered_url: Mapped[str] = mapped_column(Text, nullable=False)
    rendered_method: Mapped[str] = mapped_column(String(16), nullable=False)
    rendered_headers_json: Mapped[list[dict[str, str]]] = mapped_column(JSON, nullable=False, default=list)
    rendered_query_params_json: Mapped[list[dict[str, str]]] = mapped_column(JSON, nullable=False, default=list)
    rendered_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_body_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    item_title_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    feed_name_snapshot: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
