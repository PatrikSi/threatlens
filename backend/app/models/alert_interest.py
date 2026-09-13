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
    String,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AlertInterest(Base):
    __tablename__ = "alert_interests"

    __table_args__ = (
        CheckConstraint(
            "due_after_minutes BETWEEN 1 AND 525600",
            name="ck_alert_interests_due_minutes",
        ),
        CheckConstraint(
            "escalation_after_minutes BETWEEN 0 AND 525600",
            name="ck_alert_interests_escalation_minutes",
        ),
        CheckConstraint(
            "escalation_after_minutes IS NULL OR due_after_minutes IS NOT NULL",
            name="ck_alert_interests_escalation_due",
        ),
        CheckConstraint(
            "(user_id IS NOT NULL AND team_id IS NULL) OR (user_id IS NULL AND team_id IS NOT NULL)",
            name="ck_alert_interests_owner",
        ),
        Index("ix_alert_interests_user_id", "user_id"),
        Index("ix_alert_interests_user_id_category", "user_id", "category"),
        Index("ix_alert_interests_user_id_enabled", "user_id", "enabled"),
        Index("ix_alert_interests_enabled_durable_since", "enabled", "durable_since"),
        CheckConstraint(
            "severity IN ('low', 'medium', 'high', 'critical')",
            name="ck_alert_interests_severity",
        ),
        CheckConstraint("revision >= 1", name="ck_alert_interests_revision"),
        CheckConstraint("row_version >= 1", name="ck_alert_interests_row_version"),
        CheckConstraint(
            "(suppression_until IS NULL AND suppression_reason IS NULL) OR "
            "(suppression_until IS NOT NULL AND suppression_reason IS NOT NULL)",
            name="ck_alert_interests_suppression_pair",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("teams.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    keywords: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true", default=True
    )
    severity: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="medium", default="medium"
    )
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1", default=1
    )
    row_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1", default=1
    )
    durable_since: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    suppression_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    suppression_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    due_after_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    escalation_after_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
