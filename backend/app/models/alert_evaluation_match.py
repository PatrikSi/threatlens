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
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AlertEvaluationMatch(Base):
    __tablename__ = "alert_evaluation_matches"
    __table_args__ = (
        CheckConstraint(
            "due_after_minutes BETWEEN 1 AND 525600",
            name="ck_alert_evaluation_matches_due_minutes",
        ),
        CheckConstraint(
            "escalation_after_minutes BETWEEN 0 AND 525600",
            name="ck_alert_evaluation_matches_escalation_minutes",
        ),
        CheckConstraint(
            "escalation_after_minutes IS NULL OR due_after_minutes IS NOT NULL",
            name="ck_alert_evaluation_matches_escalation_due",
        ),
        CheckConstraint(
            "(owner_user_id IS NOT NULL AND team_id IS NULL) OR (owner_user_id IS NULL AND team_id IS NOT NULL)",
            name="ck_alert_evaluation_matches_owner",
        ),
        UniqueConstraint(
            "request_id",
            "alert_interest_id",
            "rule_revision",
            name="uq_alert_evaluation_matches_request_rule_revision",
        ),
        Index(
            "ix_alert_evaluation_matches_request_owner",
            "request_id",
            "owner_user_id",
            "id",
        ),
        Index("ix_alert_evaluation_matches_owner_user_id", "owner_user_id"),
        CheckConstraint(
            "rule_revision >= 1", name="ck_alert_evaluation_matches_rule_revision"
        ),
        CheckConstraint(
            "severity_snapshot IN ('low', 'medium', 'high', 'critical')",
            name="ck_alert_evaluation_matches_severity",
        ),
        CheckConstraint(
            "(suppressed IS FALSE AND suppression_reason IS NULL) OR "
            "(suppressed IS TRUE AND suppression_reason IS NOT NULL)",
            name="ck_alert_evaluation_matches_suppression",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    request_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("alert_evaluation_requests.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Keep the accepted rule identity even if the mutable rule is later deleted.
    alert_interest_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False
    )
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
    )
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("teams.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    rule_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    alert_name_snapshot: Mapped[str] = mapped_column(String(255), nullable=False)
    alert_category_snapshot: Mapped[str] = mapped_column(String(64), nullable=False)
    alert_keywords_snapshot: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    matched_keywords: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    severity_snapshot: Mapped[str] = mapped_column(String(16), nullable=False)
    suppressed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    suppression_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    due_after_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    escalation_after_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
