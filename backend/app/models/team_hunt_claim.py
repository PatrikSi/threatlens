"""Coordination ownership before a suggestion becomes an investigation."""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TeamHuntClaim(Base):
    __tablename__ = "team_hunt_claims"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_team_hunt_claim_version"),
    )

    assessment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("team_item_assessments.id", ondelete="CASCADE"),
        primary_key=True,
    )
    hunt_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
