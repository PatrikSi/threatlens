import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


ELEVATION_STORED_STATUSES = frozenset(
    {"pending", "approved", "denied", "cancelled", "revoked"}
)


class TemporaryElevation(Base):
    __tablename__ = "temporary_elevations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'denied', 'cancelled', 'revoked')",
            name="ck_temporary_elevations_status",
        ),
        CheckConstraint(
            "requested_duration_seconds BETWEEN 300 AND 86400",
            name="ck_temporary_elevations_duration",
        ),
        CheckConstraint(
            "length(request_reason) BETWEEN 10 AND 2000 "
            "AND btrim(request_reason) = request_reason",
            name="ck_temporary_elevations_request_reason",
        ),
        CheckConstraint(
            "decision_reason IS NULL OR (length(decision_reason) BETWEEN 3 AND 2000 "
            "AND btrim(decision_reason) = decision_reason)",
            name="ck_temporary_elevations_decision_reason",
        ),
        CheckConstraint(
            "close_reason IS NULL OR (length(close_reason) BETWEEN 3 AND 2000 "
            "AND btrim(close_reason) = close_reason)",
            name="ck_temporary_elevations_close_reason",
        ),
        CheckConstraint(
            "role_revision_snapshot >= 1",
            name="ck_temporary_elevations_role_revision",
        ),
        CheckConstraint(
            "revision >= 1",
            name="ck_temporary_elevations_revision",
        ),
        CheckConstraint(
            "request_expires_at > created_at",
            name="ck_temporary_elevations_request_expiry",
        ),
        CheckConstraint(
            "grant_expires_at IS NULL OR "
            "(grant_started_at IS NOT NULL AND grant_expires_at = grant_started_at + "
            "requested_duration_seconds * interval '1 second')",
            name="ck_temporary_elevations_grant_expiry",
        ),
        CheckConstraint(
            "decided_by_user_id IS NULL OR requested_by_user_id IS NULL OR "
            "decided_by_user_id <> requested_by_user_id",
            name="ck_temporary_elevations_no_self_decision",
        ),
        CheckConstraint(
            "decided_by_user_id IS NULL OR decided_by_user_id <> target_user_id",
            name="ck_temporary_elevations_no_target_decision",
        ),
        CheckConstraint(
            "(closed_by_principal_type IS NULL AND closed_by_user_id IS NULL "
            "AND closed_by_email_snapshot IS NULL) OR "
            "(closed_by_principal_type = 'user' "
            "AND closed_by_email_snapshot IS NOT NULL) OR "
            "(closed_by_principal_type = 'system' AND closed_by_user_id IS NULL "
            "AND closed_by_email_snapshot IS NULL)",
            name="ck_temporary_elevations_close_actor",
        ),
        CheckConstraint(
            "(status = 'pending' AND decided_by_user_id IS NULL "
            "AND decided_by_email_snapshot IS NULL AND decided_at IS NULL "
            "AND decision_reason IS NULL AND grant_started_at IS NULL "
            "AND grant_expires_at IS NULL AND closed_by_user_id IS NULL "
            "AND closed_by_principal_type IS NULL AND closed_at IS NULL "
            "AND close_reason IS NULL) OR "
            "(status = 'approved' AND decided_by_email_snapshot IS NOT NULL "
            "AND decided_at IS NOT NULL "
            "AND decision_reason IS NOT NULL "
            "AND grant_started_at IS NOT NULL AND grant_expires_at IS NOT NULL "
            "AND closed_by_user_id IS NULL AND closed_by_principal_type IS NULL "
            "AND closed_at IS NULL "
            "AND close_reason IS NULL) OR "
            "(status = 'denied' AND decided_by_email_snapshot IS NOT NULL "
            "AND decided_at IS NOT NULL "
            "AND decision_reason IS NOT NULL "
            "AND grant_started_at IS NULL AND grant_expires_at IS NULL "
            "AND closed_by_user_id IS NULL AND closed_by_principal_type IS NULL "
            "AND closed_at IS NULL "
            "AND close_reason IS NULL) OR "
            "(status = 'cancelled' AND decided_by_user_id IS NULL "
            "AND decided_by_email_snapshot IS NULL AND decided_at IS NULL "
            "AND decision_reason IS NULL "
            "AND grant_started_at IS NULL AND grant_expires_at IS NULL "
            "AND closed_by_principal_type IS NOT NULL AND closed_at IS NOT NULL "
            "AND close_reason IS NOT NULL) OR "
            "(status = 'revoked' AND decided_by_email_snapshot IS NOT NULL "
            "AND decided_at IS NOT NULL "
            "AND decision_reason IS NOT NULL "
            "AND grant_started_at IS NOT NULL AND grant_expires_at IS NOT NULL "
            "AND closed_by_principal_type IS NOT NULL AND closed_at IS NOT NULL "
            "AND close_reason IS NOT NULL)",
            name="ck_temporary_elevations_state",
        ),
        Index(
            "ix_temporary_elevations_target_status_expiry",
            "target_user_id",
            "status",
            "grant_expires_at",
        ),
        Index(
            "ix_temporary_elevations_status_request_expiry",
            "status",
            "request_expires_at",
        ),
        Index("ix_temporary_elevations_role", "role_id"),
        Index("ix_temporary_elevations_requester", "requested_by_user_id"),
        Index("ix_temporary_elevations_decider", "decided_by_user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    target_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    target_email_snapshot: Mapped[str] = mapped_column(String(320), nullable=False)
    role_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("iam_roles.id", ondelete="SET NULL"),
        nullable=True,
    )
    role_key_snapshot: Mapped[str] = mapped_column(String(64), nullable=False)
    role_name_snapshot: Mapped[str] = mapped_column(String(120), nullable=False)
    role_revision_snapshot: Mapped[int] = mapped_column(Integer, nullable=False)
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    requested_by_email_snapshot: Mapped[str] = mapped_column(
        String(320), nullable=False
    )
    requested_duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    request_reason: Mapped[str] = mapped_column(Text, nullable=False)
    request_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    decided_by_email_snapshot: Mapped[str | None] = mapped_column(
        String(320), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    grant_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    grant_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    closed_by_principal_type: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )
    closed_by_email_snapshot: Mapped[str | None] = mapped_column(
        String(320), nullable=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    close_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    permission_snapshot: Mapped[list["TemporaryElevationPermission"]] = relationship(
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class TemporaryElevationPermission(Base):
    __tablename__ = "temporary_elevation_permissions"
    __table_args__ = (
        CheckConstraint(
            "length(permission) BETWEEN 3 AND 96 AND btrim(permission) = permission",
            name="ck_temporary_elevation_permissions_value",
        ),
    )

    elevation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("temporary_elevations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    permission: Mapped[str] = mapped_column(String(96), primary_key=True)


__all__ = [
    "ELEVATION_STORED_STATUSES",
    "TemporaryElevation",
    "TemporaryElevationPermission",
]
