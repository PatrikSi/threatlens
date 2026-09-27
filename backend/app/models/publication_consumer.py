"""Opaque publication distribution ledger; evidence uses ordinary read authorization."""

from datetime import datetime
import uuid

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class PublicationConsumer(Base):
    __tablename__ = "publication_consumers"
    __table_args__ = (
        UniqueConstraint(
            "team_id", "idempotency_key", name="uq_publication_consumer_request"
        ),
        Index("ix_publication_consumer_reconcile", "last_reconciled_at", "id"),
    )
    idempotency_key: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("teams.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    principal_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    authorization_encrypted: Mapped[dict] = mapped_column(JSON, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    replay_floor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    principal_type = "user"
    source_encrypted = None


class PublicationSubscription(Base):
    __tablename__ = "publication_subscriptions"
    __table_args__ = (
        Index(
            "ix_publication_subscription_check",
            "next_check_at",
            "consumer_id",
            "publication_id",
        ),
    )
    consumer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("publication_consumers.id", ondelete="CASCADE"),
        primary_key=True,
    )
    publication_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("indicator_publications.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    last_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_check_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PublicationChange(Base):
    __tablename__ = "publication_changes"
    __table_args__ = (
        UniqueConstraint(
            "consumer_id", "sequence", name="uq_publication_change_sequence"
        ),
        Index("ix_publication_change_ack", "acknowledged_at", "created_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    consumer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("publication_consumers.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    publication_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
