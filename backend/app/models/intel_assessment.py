"""Versioned indicator observations, team verdicts and exact-match exclusions."""

from datetime import datetime
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ItemIntelState(Base):
    __tablename__ = "item_intel_states"
    item_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("items.id", ondelete="CASCADE"), primary_key=True
    )
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    source_revision: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    handling_label_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("handling_labels.id", ondelete="RESTRICT")
    )
    source_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", server_default=""
    )
    indicator_set_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", server_default=""
    )
    extraction_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", server_default=""
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class IndicatorAssessment(Base):
    __tablename__ = "indicator_assessments"
    __table_args__ = (
        UniqueConstraint(
            "team_id", "item_id", "ioc_id", name="uq_indicator_assessments_scope"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ioc_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("iocs.id", ondelete="CASCADE"), nullable=False
    )
    handling_label_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("handling_labels.id", ondelete="RESTRICT"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    source_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    extraction_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    verdict: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class IndicatorAssessmentHistory(Base):
    __tablename__ = "indicator_assessment_history"
    __table_args__ = (
        UniqueConstraint(
            "assessment_id", "version", name="uq_indicator_assessment_history_version"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    assessment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("indicator_assessments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class IndicatorSuppression(Base):
    __tablename__ = "indicator_suppressions"
    __table_args__ = (
        UniqueConstraint(
            "team_id",
            "ioc_type",
            "value_digest",
            name="uq_indicator_suppressions_scope",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ioc_type: Mapped[str] = mapped_column(String(32), nullable=False)
    value_norm: Mapped[str] = mapped_column(Text, nullable=False)
    value_digest: Mapped[str] = mapped_column(
        String(64),
        Computed("threatlens_indicator_digest(value_norm)", persisted=True),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class IndicatorSuppressionHistory(Base):
    __tablename__ = "indicator_suppression_history"
    __table_args__ = (
        UniqueConstraint(
            "suppression_id", "version", name="uq_indicator_suppression_history_version"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    suppression_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("indicator_suppressions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TeamIntelState(Base):
    __tablename__ = "team_intel_states"
    team_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("items.id", ondelete="CASCADE"), primary_key=True
    )
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    indicator_set_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", server_default=""
    )
