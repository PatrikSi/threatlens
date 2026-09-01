import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    __table_args__ = (
        Index("ix_audit_logs_actor_user_id", "actor_user_id"),
        Index("ix_audit_logs_action", "action"),
        Index("ix_audit_logs_created_at", "created_at"),
        Index(
            "ix_audit_logs_actor_principal",
            "actor_principal_type",
            "actor_principal_id",
            "created_at",
        ),
        Index("ix_audit_logs_request_id", "request_id"),
        Index(
            "ix_audit_logs_credential_created",
            "credential_id",
            "credential_kind",
            "created_at",
        ),
        Index(
            "ix_audit_logs_resource_created",
            "resource_type",
            "resource_id",
            "created_at",
        ),
        Index("ix_audit_logs_success_created", "success", "created_at"),
        Index(
            "ix_audit_logs_authorization_approval",
            "authorization_approval_id",
            "created_at",
        ),
        Index(
            "ix_audit_logs_execution_receipt",
            "execution_receipt_id",
            "created_at",
        ),
        CheckConstraint(
            "jsonb_typeof(authorization_elevation_ids) = 'array' AND "
            "NOT jsonb_path_exists(authorization_elevation_ids, "
            "'$[*] ? (@.type() != \"string\")')",
            name="ck_audit_logs_authorization_elevation_ids",
        ),
        CheckConstraint(
            "jsonb_typeof(data_access_label_ids) = 'array' AND "
            "NOT jsonb_path_exists(data_access_label_ids, "
            "'$[*] ? (@.type() != \"string\")')",
            name="ck_audit_logs_data_access_label_ids",
        ),
        CheckConstraint(
            "data_access_governed OR jsonb_array_length(data_access_label_ids) = 0",
            name="ck_audit_logs_ungoverned_labels_empty",
        ),
        Index(
            "ix_audit_logs_authorization_elevation_ids",
            "authorization_elevation_ids",
            postgresql_using="gin",
        ),
        Index(
            "ix_audit_logs_data_access_label_ids",
            "data_access_label_ids",
            postgresql_using="gin",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_principal_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    actor_principal_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )
    actor_label_snapshot: Mapped[str | None] = mapped_column(
        String(320), nullable=True
    )
    credential_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    credential_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    authorization_elevation_ids: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
        default=list,
        server_default="[]",
    )
    authorization_approval_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )
    execution_receipt_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )
    data_access_governed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    data_access_label_ids: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
        default=list,
        server_default="[]",
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    resource_type: Mapped[str] = mapped_column(Text, nullable=False)
    resource_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    resource_label_snapshot: Mapped[str | None] = mapped_column(
        String(320), nullable=True
    )
    success: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    metadata_json: Mapped[dict] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AuditLogDataAccessFeed(Base):
    __tablename__ = "audit_log_data_access_feeds"
    __table_args__ = (
        Index(
            "ix_audit_log_data_access_feeds_feed",
            "source_feed_id_snapshot",
            "audit_log_id",
        ),
    )

    audit_log_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("audit_logs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    source_feed_id_snapshot: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True
    )


class AuditLogDataAccessLabel(Base):
    __tablename__ = "audit_log_data_access_labels"
    __table_args__ = (
        Index(
            "ix_audit_log_data_access_labels_label",
            "label_id",
            "audit_log_id",
        ),
    )

    audit_log_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("audit_logs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    label_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("handling_labels.id", ondelete="RESTRICT"),
        primary_key=True,
    )
