import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ServiceAccount(Base):
    __tablename__ = "service_accounts"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_service_accounts_revision"),
        CheckConstraint(
            "(is_active AND disabled_at IS NULL) OR "
            "(NOT is_active AND disabled_at IS NOT NULL)",
            name="ck_service_accounts_active_state",
        ),
        UniqueConstraint("key", name="uq_service_accounts_key"),
        Index("ix_service_accounts_active_name", "is_active", "name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    disabled_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    disabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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


class ServiceAccountCredential(Base):
    __tablename__ = "service_account_credentials"
    __table_args__ = (
        CheckConstraint(
            "token_prefix LIKE 'tlsa\\_%' ESCAPE '\\'",
            name="ck_service_account_credentials_prefix",
        ),
        CheckConstraint(
            "length(token_hash) = 64",
            name="ck_service_account_credentials_hash_length",
        ),
        CheckConstraint(
            "operation_key_hash IS NULL OR length(operation_key_hash) = 64",
            name="ck_service_account_credentials_operation_key_hash_length",
        ),
        CheckConstraint(
            "operation_request_hash IS NULL OR length(operation_request_hash) = 64",
            name="ck_service_account_credentials_operation_request_hash_length",
        ),
        CheckConstraint(
            "(operation_kind IS NULL AND operation_key_hash IS NULL AND "
            "operation_request_hash IS NULL) OR "
            "(operation_kind IN ('issue', 'rotate') AND "
            "operation_key_hash IS NOT NULL AND operation_request_hash IS NOT NULL)",
            name="ck_service_account_credentials_operation_receipt",
        ),
        CheckConstraint(
            "jsonb_typeof(scopes) = 'array' AND jsonb_array_length(scopes) > 0 "
            "AND NOT jsonb_path_exists(scopes, "
            "'$[*] ? (@.type() != \"string\")')",
            name="ck_service_account_credentials_scopes_array",
        ),
        CheckConstraint(
            "expires_at > created_at",
            name="ck_service_account_credentials_expiry",
        ),
        CheckConstraint(
            "original_expires_at IS NULL OR original_expires_at >= expires_at",
            name="ck_service_account_credentials_original_expiry",
        ),
        UniqueConstraint("token_prefix", name="uq_service_account_credentials_prefix"),
        UniqueConstraint("token_hash", name="uq_service_account_credentials_hash"),
        UniqueConstraint(
            "service_account_id",
            "operation_key_hash",
            name="uq_service_account_credentials_operation_key",
        ),
        Index(
            "ix_service_account_credentials_account_created",
            "service_account_id",
            "created_at",
        ),
        Index(
            "ix_service_account_credentials_active_expiry",
            "service_account_id",
            "revoked_at",
            "expires_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    service_account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("service_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    rotated_from_credential_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("service_account_credentials.id", ondelete="SET NULL"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    token_prefix: Mapped[str] = mapped_column(String(32), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    operation_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    operation_key_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    operation_request_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    scopes: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    original_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_used_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_used_user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ServiceAccountRoleAssignment(Base):
    __tablename__ = "service_account_role_assignments"
    __table_args__ = (
        UniqueConstraint(
            "service_account_id",
            "role_id",
            name="uq_service_account_role_assignments",
        ),
        Index("ix_service_account_role_assignments_role", "role_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    service_account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("service_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("iam_roles.id", ondelete="RESTRICT"),
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
