import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging_config import get_log_context
from app.models.audit_log import AuditLog


def record_audit(
    db: Session,
    *,
    actor_user_id: uuid.UUID | None,
    actor_principal_type: str | None = None,
    actor_principal_id: uuid.UUID | None = None,
    credential_kind: str | None = None,
    credential_id: uuid.UUID | None = None,
    request_id: str | None = None,
    source_ip: str | None = None,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    success: bool = True,
    metadata: dict[str, Any] | None = None,
) -> AuditLog:
    request_context = get_log_context()
    resolved_principal_type = actor_principal_type
    if resolved_principal_type is None:
        resolved_principal_type = request_context.get("actor_principal_type")
    if resolved_principal_type is None:
        resolved_principal_type = "user" if actor_user_id is not None else "system"
    resolved_principal_id = actor_principal_id
    if resolved_principal_id is None:
        resolved_principal_id = _uuid_or_none(
            request_context.get("actor_principal_id")
        )
    if resolved_principal_id is None and resolved_principal_type == "user":
        resolved_principal_id = actor_user_id
    resolved_credential_id = credential_id or _uuid_or_none(
        request_context.get("credential_id")
    )
    log = AuditLog(
        actor_user_id=actor_user_id,
        actor_principal_type=resolved_principal_type,
        actor_principal_id=resolved_principal_id,
        credential_kind=credential_kind or request_context.get("credential_kind"),
        credential_id=resolved_credential_id,
        request_id=request_id or request_context.get("request_id"),
        source_ip=source_ip or request_context.get("source_ip"),
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        success=success,
        metadata_json=metadata or {},
    )
    db.add(log)
    db.flush()
    return log


def _uuid_or_none(value: object) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None
