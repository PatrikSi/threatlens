import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.services.feed_storage import decrypt_feed_url, encrypt_feed_url, feed_url_digest


class Feed(Base):
    __tablename__ = "feeds"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, nullable=False)
    _url_encrypted: Mapped[str] = mapped_column("url", Text, nullable=False)
    url_digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    site_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[str | None] = mapped_column(String(64), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    fetch_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="interval", server_default="interval")
    fetch_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=1800, server_default="1800")
    schedule_cron: Mapped[str | None] = mapped_column(Text, nullable=True)
    etag: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_modified: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_fetch_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dispatch_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    dispatch_backoff_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    @property
    def url(self) -> str:
        return decrypt_feed_url(self._url_encrypted) or ""

    @url.setter
    def url(self, value: str) -> None:
        if not value:
            raise ValueError("Feed URL cannot be empty")
        digest = feed_url_digest(value)
        if digest is None:  # pragma: no cover - defensive only
            raise ValueError("Feed URL cannot be empty")
        self._url_encrypted = encrypt_feed_url(value)
        self.url_digest = digest
