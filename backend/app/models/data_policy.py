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
QUARANTINE_HANDLING_LABEL_ID = uuid.UUID("00000000-0000-4000-8000-000000000202")


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


class DataAccessEnvelope(Base):
    __tablename__ = "data_access_envelopes"
    __table_args__ = (
        CheckConstraint(
            "resource_type = lower(resource_type) AND "
            "resource_type ~ '^[a-z][a-z0-9_]{0,63}$'",
            name="ck_data_access_envelopes_resource_type",
        ),
        CheckConstraint(
            "source_count >= 0", name="ck_data_access_envelopes_source_count"
        ),
        CheckConstraint(
            "policy_revision >= 1",
            name="ck_data_access_envelopes_policy_revision",
        ),
        UniqueConstraint(
            "resource_type",
            "resource_id",
            name="uq_data_access_envelopes_resource",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    policy_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class DataAccessEnvelopeLabel(Base):
    __tablename__ = "data_access_envelope_labels"
    __table_args__ = (
        CheckConstraint(
            "source_count >= 1",
            name="ck_data_access_envelope_labels_source_count",
        ),
        Index(
            "ix_data_access_envelope_labels_label",
            "label_id",
            "envelope_id",
        ),
    )

    envelope_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("data_access_envelopes.id", ondelete="CASCADE"),
        primary_key=True,
    )
    label_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("handling_labels.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    source_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )


class DataAccessEnvelopeSource(Base):
    __tablename__ = "data_access_envelope_sources"
    __table_args__ = (
        CheckConstraint(
            "source_type = lower(source_type) AND "
            "source_type ~ '^[a-z][a-z0-9_]{0,63}$'",
            name="ck_data_access_envelope_sources_type",
        ),
        CheckConstraint(
            "source_id = btrim(source_id) AND length(source_id) BETWEEN 1 AND 512 "
            "AND source_id !~ '[[:cntrl:]]'",
            name="ck_data_access_envelope_sources_id",
        ),
        CheckConstraint(
            "source_version = btrim(source_version) "
            "AND length(source_version) BETWEEN 1 AND 128 "
            "AND source_version !~ '[[:cntrl:]]'",
            name="ck_data_access_envelope_sources_version",
        ),
        CheckConstraint(
            "captured_policy_revision >= 1",
            name="ck_data_access_envelope_sources_policy_revision",
        ),
        CheckConstraint(
            "source_digest IS NULL OR source_digest ~ '^[0-9a-f]{64}$'",
            name="ck_data_access_envelope_sources_digest",
        ),
        UniqueConstraint(
            "envelope_id",
            "source_type",
            "source_id",
            "source_version",
            "source_parent_id",
            name="uq_data_access_envelope_sources_identity",
            postgresql_nulls_not_distinct=True,
        ),
        Index(
            "ix_data_access_envelope_sources_feed_envelope",
            "source_feed_id",
            "envelope_id",
        ),
        Index(
            "ix_data_access_envelope_sources_label_envelope",
            "handling_label_id",
            "envelope_id",
        ),
        Index(
            "ix_data_access_envelope_sources_parent_source",
            "source_parent_id",
            "envelope_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    envelope_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("data_access_envelopes.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(512), nullable=False)
    source_version: Mapped[str] = mapped_column(String(128), nullable=False)
    source_feed_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("feeds.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_parent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("data_access_envelope_sources.id", ondelete="RESTRICT"),
        nullable=True,
    )
    handling_label_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("handling_labels.id", ondelete="RESTRICT"),
        nullable=False,
    )
    captured_policy_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    source_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


__all__ = [
    "DataAccessEnvelope",
    "DataAccessEnvelopeLabel",
    "DataAccessEnvelopeSource",
    "DataPolicyRoleGrant",
    "DataPolicyState",
    "HandlingLabel",
    "QUARANTINE_HANDLING_LABEL_ID",
    "UNRESTRICTED_HANDLING_LABEL_ID",
]
