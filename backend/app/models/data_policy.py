import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


UNRESTRICTED_HANDLING_LABEL_ID = uuid.UUID("00000000-0000-4000-8000-000000000201")


class DataPolicyState(Base):
    __tablename__ = "data_policy_state"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_data_policy_state_singleton"),
        CheckConstraint(
            "mode IN ('disabled', 'audit', 'enforced')",
            name="ck_data_policy_state_mode",
        ),
        CheckConstraint("revision >= 1", name="ck_data_policy_state_revision"),
        CheckConstraint(
            "coverage_version >= 0", name="ck_data_policy_state_coverage_version"
        ),
        CheckConstraint(
            "(mode = 'enforced' AND enforced_at IS NOT NULL "
            "AND enforced_by_user_id IS NOT NULL) OR "
            "(mode <> 'enforced' AND enforced_at IS NULL "
            "AND enforced_by_user_id IS NULL)",
            name="ck_data_policy_state_enforcement_bundle",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="disabled", server_default="disabled"
    )
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    coverage_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    enforced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    enforced_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class HandlingLabel(Base):
    __tablename__ = "handling_labels"
    __table_args__ = (
        CheckConstraint(
            "key = lower(key) AND key = btrim(key) "
            "AND key ~ '^[a-z][a-z0-9]*([._-][a-z0-9]+)*$'",
            name="ck_handling_labels_key",
        ),
        CheckConstraint(
            "name = btrim(name) AND length(name) BETWEEN 1 AND 120 "
            "AND name !~ '[[:cntrl:]]'",
            name="ck_handling_labels_name",
        ),
        CheckConstraint(
            "description = btrim(description) AND length(description) <= 2000",
            name="ck_handling_labels_description",
        ),
        CheckConstraint("color ~ '^#[0-9A-Fa-f]{6}$'", name="ck_handling_labels_color"),
        CheckConstraint("revision >= 1", name="ck_handling_labels_revision"),
        CheckConstraint(
            "(id = '00000000-0000-4000-8000-000000000201'::uuid "
            "AND key = 'unrestricted' AND is_unrestricted AND is_system "
            "AND is_active) OR "
            "(id <> '00000000-0000-4000-8000-000000000201'::uuid "
            "AND NOT is_unrestricted)",
            name="ck_handling_labels_unrestricted_identity",
        ),
        UniqueConstraint("key", name="uq_handling_labels_key"),
        Index("ix_handling_labels_active_name", "is_active", "name"),
        Index(
            "uq_handling_labels_unrestricted",
            "is_unrestricted",
            unique=True,
            postgresql_where=text("is_unrestricted"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    color: Mapped[str] = mapped_column(
        String(7), nullable=False, default="#64748B", server_default="#64748B"
    )
    is_unrestricted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    is_system: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class DataPolicyRoleGrant(Base):
    __tablename__ = "data_policy_role_grants"
    __table_args__ = (Index("ix_data_policy_role_grants_role", "role_id", "label_id"),)

    label_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("handling_labels.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("iam_roles.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    granted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


__all__ = [
    "DataPolicyRoleGrant",
    "DataPolicyState",
    "HandlingLabel",
    "UNRESTRICTED_HANDLING_LABEL_ID",
]
