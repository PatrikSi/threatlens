from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.iam import IAMGroup, IAMRole


# The composite target FKs below require these indexes. Keeping the index objects
# in model metadata preserves parity with the Alembic migration and blocks a mapped
# custom target from later being changed into a system target.
_iam_role_oidc_target_index = Index(
    "ux_iam_roles_id_is_system_oidc",
    IAMRole.__table__.c.id,
    IAMRole.__table__.c.is_system,
    unique=True,
)
_iam_group_oidc_target_index = Index(
    "ux_iam_groups_id_is_system_oidc",
    IAMGroup.__table__.c.id,
    IAMGroup.__table__.c.is_system,
    unique=True,
)


def _new_role_source_key() -> str:
    return f"oidc:role:{uuid.uuid4().hex}"


def _new_group_source_key() -> str:
    return f"oidc:group:{uuid.uuid4().hex}"


class OIDCAccessPolicy(Base):
    __tablename__ = "oidc_access_policies"
    __table_args__ = (
        CheckConstraint(
            "revision >= 1",
            name="ck_oidc_access_policies_revision",
        ),
        UniqueConstraint(
            "provider_id",
            name="uq_oidc_access_policies_provider",
        ),
        Index("ix_oidc_access_policies_updated_by", "updated_by_user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    provider_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "oidc_providers.id",
            ondelete="CASCADE",
            name="fk_oidc_access_policies_provider",
        ),
        nullable=False,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "users.id",
            ondelete="SET NULL",
            name="fk_oidc_access_policies_updated_by",
        ),
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


class OIDCClaimMappingSet(Base):
    __tablename__ = "oidc_claim_mapping_sets"
    __table_args__ = (
        CheckConstraint(
            "key ~ '^[a-z][a-z0-9-]{1,62}[a-z0-9]$'",
            name="ck_oidc_claim_mapping_sets_key",
        ),
        CheckConstraint(
            "name = btrim(name) AND length(name) > 0 AND name !~ '[[:cntrl:]]'",
            name="ck_oidc_claim_mapping_sets_name",
        ),
        CheckConstraint(
            "claim_path ~ '^[A-Za-z0-9_:-]+([.][A-Za-z0-9_:-]+)*$'",
            name="ck_oidc_claim_mapping_sets_claim_path",
        ),
        CheckConstraint(
            "missing_claim_behavior IN ('preserve', 'remove', 'deny')",
            name="ck_oidc_claim_mapping_sets_missing_behavior",
        ),
        CheckConstraint(
            "revision >= 1",
            name="ck_oidc_claim_mapping_sets_revision",
        ),
        UniqueConstraint(
            "access_policy_id",
            "key",
            name="uq_oidc_claim_mapping_sets_policy_key",
        ),
        Index(
            "ix_oidc_claim_mapping_sets_policy_enabled_name",
            "access_policy_id",
            "enabled",
            "name",
        ),
        Index("ix_oidc_claim_mapping_sets_updated_by", "updated_by_user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    access_policy_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "oidc_access_policies.id",
            ondelete="CASCADE",
            name="fk_oidc_claim_mapping_sets_policy",
        ),
        nullable=False,
    )
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    claim_path: Mapped[str] = mapped_column(String(255), nullable=False)
    missing_claim_behavior: Mapped[str] = mapped_column(
        String(16), nullable=False, default="preserve", server_default="preserve"
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "users.id",
            ondelete="SET NULL",
            name="fk_oidc_claim_mapping_sets_updated_by",
        ),
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


class OIDCRoleClaimMapping(Base):
    __tablename__ = "oidc_role_claim_mappings"
    __table_args__ = (
        CheckConstraint(
            "source_key ~ '^oidc:role:[0-9a-f]{32}$'",
            name="ck_oidc_role_claim_mappings_source_key",
        ),
        CheckConstraint(
            "length(claim_value) > 0 AND claim_value = btrim(claim_value) "
            "AND claim_value !~ '[[:cntrl:]]'",
            name="ck_oidc_role_claim_mappings_claim_value",
        ),
        CheckConstraint(
            "NOT role_is_system",
            name="ck_oidc_role_claim_mappings_custom_role",
        ),
        ForeignKeyConstraint(
            ["role_id", "role_is_system"],
            ["iam_roles.id", "iam_roles.is_system"],
            ondelete="RESTRICT",
            name="fk_oidc_role_claim_mappings_custom_role",
        ),
        UniqueConstraint(
            "source_key",
            name="uq_oidc_role_claim_mappings_source_key",
        ),
        UniqueConstraint(
            "mapping_set_id",
            "claim_value",
            name="uq_oidc_role_claim_mappings_set_value",
        ),
        UniqueConstraint(
            "id",
            "source_key",
            "role_id",
            name="uq_oidc_role_claim_mappings_grant_owner",
        ),
        Index("ix_oidc_role_claim_mappings_role", "role_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    mapping_set_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "oidc_claim_mapping_sets.id",
            ondelete="CASCADE",
            name="fk_oidc_role_claim_mappings_set",
        ),
        nullable=False,
    )
    source_key: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=False, default=_new_role_source_key
    )
    claim_value: Mapped[str] = mapped_column(String(512, collation="C"), nullable=False)
    role_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    role_is_system: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
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


class OIDCGroupClaimMapping(Base):
    __tablename__ = "oidc_group_claim_mappings"
    __table_args__ = (
        CheckConstraint(
            "source_key ~ '^oidc:group:[0-9a-f]{32}$'",
            name="ck_oidc_group_claim_mappings_source_key",
        ),
        CheckConstraint(
            "length(claim_value) > 0 AND claim_value = btrim(claim_value) "
            "AND claim_value !~ '[[:cntrl:]]'",
            name="ck_oidc_group_claim_mappings_claim_value",
        ),
        CheckConstraint(
            "NOT group_is_system",
            name="ck_oidc_group_claim_mappings_custom_group",
        ),
        ForeignKeyConstraint(
            ["group_id", "group_is_system"],
            ["iam_groups.id", "iam_groups.is_system"],
            ondelete="RESTRICT",
            name="fk_oidc_group_claim_mappings_custom_group",
        ),
        UniqueConstraint(
            "source_key",
            name="uq_oidc_group_claim_mappings_source_key",
        ),
        UniqueConstraint(
            "mapping_set_id",
            "claim_value",
            name="uq_oidc_group_claim_mappings_set_value",
        ),
        UniqueConstraint(
            "id",
            "source_key",
            "group_id",
            name="uq_oidc_group_claim_mappings_grant_owner",
        ),
        Index("ix_oidc_group_claim_mappings_group", "group_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    mapping_set_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "oidc_claim_mapping_sets.id",
            ondelete="CASCADE",
            name="fk_oidc_group_claim_mappings_set",
        ),
        nullable=False,
    )
    source_key: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=False, default=_new_group_source_key
    )
    claim_value: Mapped[str] = mapped_column(String(512, collation="C"), nullable=False)
    group_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    group_is_system: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
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
