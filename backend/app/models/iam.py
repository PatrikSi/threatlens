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
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class IAMPolicyState(Base):
    __tablename__ = "iam_policy_state"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_iam_policy_state_singleton"),
        CheckConstraint("revision >= 1", name="ck_iam_policy_state_revision"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class IAMRole(Base):
    __tablename__ = "iam_roles"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_iam_roles_revision"),
        UniqueConstraint("key", name="uq_iam_roles_key"),
        Index("ix_iam_roles_system_name", "is_system", "name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_system: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
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


class IAMRolePermission(Base):
    __tablename__ = "iam_role_permissions"
    __table_args__ = (Index("ix_iam_role_permissions_permission", "permission"),)

    role_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("iam_roles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    permission: Mapped[str] = mapped_column(String(96), primary_key=True)


class IAMUserRoleAssignment(Base):
    __tablename__ = "iam_user_role_assignments"
    __table_args__ = (
        CheckConstraint(
            "source IN ('local', 'oidc')",
            name="ck_iam_user_role_assignments_source",
        ),
        CheckConstraint(
            "(source = 'local' AND source_key = '') OR "
            "(source = 'oidc' AND length(source_key) > 0)",
            name="ck_iam_user_role_assignments_source_key",
        ),
        UniqueConstraint(
            "user_id",
            "role_id",
            "source",
            "source_key",
            name="uq_iam_user_role_assignments_origin",
        ),
        Index("ix_iam_user_role_assignments_user", "user_id"),
        Index("ix_iam_user_role_assignments_role", "role_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("iam_roles.id", ondelete="CASCADE"),
        nullable=False,
    )
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="local", server_default="local"
    )
    source_key: Mapped[str] = mapped_column(
        String(255), nullable=False, default="", server_default=""
    )
    assigned_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class IAMGroup(Base):
    __tablename__ = "iam_groups"
    __table_args__ = (
        CheckConstraint("source IN ('local', 'oidc')", name="ck_iam_groups_source"),
        CheckConstraint(
            "(source = 'local' AND external_key IS NULL) OR "
            "(source = 'oidc' AND external_key IS NOT NULL AND length(external_key) > 0)",
            name="ck_iam_groups_external_key",
        ),
        CheckConstraint("revision >= 1", name="ck_iam_groups_revision"),
        UniqueConstraint("key", name="uq_iam_groups_key"),
        UniqueConstraint(
            "source", "external_key", name="uq_iam_groups_external_origin"
        ),
        Index("ix_iam_groups_source_name", "source", "name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="local", server_default="local"
    )
    external_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_system: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
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


class IAMGroupMembership(Base):
    __tablename__ = "iam_group_memberships"
    __table_args__ = (
        CheckConstraint(
            "source IN ('local', 'oidc')",
            name="ck_iam_group_memberships_source",
        ),
        CheckConstraint(
            "(source = 'local' AND source_key = '') OR "
            "(source = 'oidc' AND length(source_key) > 0)",
            name="ck_iam_group_memberships_source_key",
        ),
        UniqueConstraint(
            "group_id",
            "user_id",
            "source",
            "source_key",
            name="uq_iam_group_memberships_origin",
        ),
        Index("ix_iam_group_memberships_user", "user_id"),
        Index("ix_iam_group_memberships_group", "group_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    group_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("iam_groups.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="local", server_default="local"
    )
    source_key: Mapped[str] = mapped_column(
        String(255), nullable=False, default="", server_default=""
    )
    assigned_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class IAMGroupRoleAssignment(Base):
    __tablename__ = "iam_group_role_assignments"
    __table_args__ = (
        UniqueConstraint("group_id", "role_id", name="uq_iam_group_role_assignments"),
        Index("ix_iam_group_role_assignments_role", "role_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    group_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("iam_groups.id", ondelete="CASCADE"),
        nullable=False,
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("iam_roles.id", ondelete="CASCADE"),
        nullable=False,
    )
    assigned_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
