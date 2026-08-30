import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class GovernanceOperationReceipt(Base):
    __tablename__ = "governance_operation_receipts"
    __table_args__ = (
        CheckConstraint(
            "length(key_hash) = 64",
            name="ck_governance_operation_receipts_key_hash",
        ),
        CheckConstraint(
            "length(request_fingerprint) = 64",
            name="ck_governance_operation_receipts_fingerprint",
        ),
        CheckConstraint(
            "http_status BETWEEN 200 AND 499",
            name="ck_governance_operation_receipts_http_status",
        ),
        CheckConstraint(
            "response_schema_version >= 1",
            name="ck_governance_operation_receipts_schema_version",
        ),
        UniqueConstraint(
            "actor_user_id",
            "operation",
            "key_hash",
            name="uq_governance_operation_receipts_actor_operation_key",
        ),
        Index(
            "ix_governance_operation_receipts_resource",
            "resource_type",
            "resource_id",
        ),
        Index(
            "ix_governance_operation_receipts_created",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    actor_user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    operation: Mapped[str] = mapped_column(String(96), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    response_json: Mapped[dict] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False
    )
    response_schema_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    http_status: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


__all__ = ["GovernanceOperationReceipt"]
