import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.services.feed_storage import encrypt_feed_url, feed_url_digest, try_decrypt_feed_url


class Feed(Base):
    __tablename__ = "feeds"
    __table_args__ = (Index("ix_feeds_enabled_next_fetch_at", "enabled", "next_fetch_at"),)

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
    next_fetch_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dispatch_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    dispatch_backoff_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    fetch_fence: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default="0",
    )
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    handling_label_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("handling_labels.id", ondelete="RESTRICT"),
        nullable=False,
        server_default="00000000-0000-4000-8000-000000000201",
        index=True,
    )

    @property
    def url(self) -> str:
        plaintext, _error = try_decrypt_feed_url(self._url_encrypted)
        return plaintext or ""

    @property
    def url_decryption_error(self) -> str | None:
        _plaintext, error = try_decrypt_feed_url(self._url_encrypted)
        return error

    @url.setter
    def url(self, value: str) -> None:
        if not value:
            raise ValueError("Feed URL cannot be empty")
        digest = feed_url_digest(value)
        if digest is None:  # pragma: no cover - defensive only
            raise ValueError("Feed URL cannot be empty")
        self._url_encrypted = encrypt_feed_url(value)
        self.url_digest = digest
