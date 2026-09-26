"""Account-wide AI limits and durable, bounded fair-admission turns."""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AIQuotaGroup(Base):
    __tablename__ = "ai_quota_groups"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_ai_quota_group_version"),
        CheckConstraint(
            "max_concurrent_requests >= 0 AND hourly_token_budget >= 0 AND max_concurrent_per_team >= 0",
            name="ck_ai_quota_group_limits",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    normalized_name: Mapped[str] = mapped_column(
        String(360), nullable=False, unique=True
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    max_concurrent_requests: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    hourly_token_budget: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    max_concurrent_per_team: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AIQuotaGroupMember(Base):
    __tablename__ = "ai_quota_group_members"

    # Deliberately no provider FK: quota administration never enters the profile
    # lock graph held across provider I/O. Provider IDs cannot be reused.
    provider_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    group_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("ai_quota_groups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


class AIQuotaTeamTurn(Base):
    __tablename__ = "ai_quota_team_turns"
    __table_args__ = (Index("ix_ai_quota_turn_expiry", "waiting_until"),)

    group_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("ai_quota_groups.id", ondelete="CASCADE"),
        primary_key=True,
    )
    team_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    waiting_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    waiting_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_served_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
