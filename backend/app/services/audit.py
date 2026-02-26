import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog


def record_audit(
    db: Session,
    *,
    actor_user_id: uuid.UUID | None,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    success: bool = True,
    metadata: dict[str, Any] | None = None,
) -> AuditLog:
    log = AuditLog(
        actor_user_id=actor_user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        success=success,
        metadata_json=metadata or {},
    )
    db.add(log)
    db.flush()
    return log
