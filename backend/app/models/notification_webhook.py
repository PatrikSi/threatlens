import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class NotificationWebhook(Base):
    __tablename__ = "notification_webhooks"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    integration_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("integration_instances.id", ondelete="CASCADE"),
        nullable=True,
        unique=True,
    )
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("integration_subscriptions.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, default="rss_item_new", server_default="rss_item_new")
    url_template: Mapped[str] = mapped_column(Text, nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False, default="POST", server_default="POST")
    feed_scope: Mapped[str] = mapped_column(String(16), nullable=False, default="all", server_default="all")
    feed_ids_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    query_params_json: Mapped[list[dict[str, str]]] = mapped_column(JSON, nullable=False, default=list)
    headers_json: Mapped[list[dict[str, str]]] = mapped_column(JSON, nullable=False, default=list)
    body_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="json", server_default="json")
    body_fields_json: Mapped[list[dict[str, str]]] = mapped_column(JSON, nullable=False, default=list)
    body_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=10, server_default="10")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
