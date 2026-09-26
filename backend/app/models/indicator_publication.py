"""Immutable reviewed export snapshots and monotonic withdrawal history."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class IndicatorPublication(Base):
    __tablename__ = "indicator_publications"
    __table_args__ = (
        UniqueConstraint("team_id", "idempotency_key", name="uq_indicator_publication_request"),
        Index("ix_indicator_publication_team_created", "team_id", "created_at", "id"),
        Index("ix_indicator_publication_check", "next_check_at", "id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    idempotency_key: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    format: Mapped[str] = mapped_column(String(8), nullable=False)
    marking: Mapped[str] = mapped_column(String(16), nullable=False)
    distribution: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    snapshot_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    indicator_count: Mapped[int] = mapped_column(Integer, nullable=False)
    withdrawn_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    next_check_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class IndicatorPublicationLabel(Base):
    __tablename__ = "indicator_publication_labels"
    publication_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("indicator_publications.id", ondelete="CASCADE"), primary_key=True)
    handling_label_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("handling_labels.id", ondelete="RESTRICT"), primary_key=True)


class IndicatorPublicationSource(Base):
    __tablename__ = "indicator_publication_sources"
    publication_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("indicator_publications.id", ondelete="CASCADE"), primary_key=True)
    # Source identity survives normal article retention; missing sources withhold
    # the stored evidence rather than silently broadening historical access.
    item_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    feed_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
