import uuid
from collections.abc import Iterable
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
    authorization_approval_id: uuid.UUID | None = None,
    execution_receipt_id: uuid.UUID | None = None,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    success: bool = True,
    metadata: dict[str, Any] | None = None,
    data_access_governed: bool | None = None,
    data_access_label_ids: Iterable[uuid.UUID | str] | None = None,
) -> AuditLog:
    request_context = get_log_context()
    resolved_principal_type = actor_principal_type
    if resolved_principal_type is None:
        resolved_principal_type = request_context.get("actor_principal_type")
    if resolved_principal_type is None:
        resolved_principal_type = "user" if actor_user_id is not None else "system"
    if resolved_principal_type == "service_account":
        # Machine principal IDs do not reference users.id. Request handlers written
        # before service accounts may still pass their principal ID through the
        # actor_user_id argument, so normalize it before constructing the FK row.
        actor_user_id = None
    resolved_principal_id = actor_principal_id
    if resolved_principal_id is None:
        resolved_principal_id = _uuid_or_none(request_context.get("actor_principal_id"))
    if resolved_principal_id is None and resolved_principal_type == "user":
        resolved_principal_id = actor_user_id
    resolved_credential_id = credential_id or _uuid_or_none(
        request_context.get("credential_id")
    )
    resolved_metadata = dict(metadata or {})
    raw_elevation_ids = request_context.get("authorization_elevation_ids")
    elevation_ids = (
        [value for value in raw_elevation_ids.split(",") if value]
        if raw_elevation_ids
        else []
    )
    if elevation_ids:
        resolved_metadata.setdefault(
            "authorization_elevation_ids",
            elevation_ids,
        )
    governed, label_ids = _resolve_data_access_snapshot(
        db,
        resource_type=resource_type,
        resource_id=resource_id,
        data_access_governed=data_access_governed,
        data_access_label_ids=data_access_label_ids,
    )
    log = AuditLog(
        actor_user_id=actor_user_id,
        actor_principal_type=resolved_principal_type,
        actor_principal_id=resolved_principal_id,
        credential_kind=credential_kind or request_context.get("credential_kind"),
        credential_id=resolved_credential_id,
        request_id=request_id or request_context.get("request_id"),
        source_ip=source_ip or request_context.get("source_ip"),
        authorization_elevation_ids=elevation_ids,
        authorization_approval_id=authorization_approval_id,
        execution_receipt_id=execution_receipt_id,
        data_access_governed=governed,
        data_access_label_ids=label_ids,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        success=success,
        metadata_json=resolved_metadata,
    )
    db.add(log)
    db.flush()
    return log


def _resolve_data_access_snapshot(
    db: Session,
    *,
    resource_type: str,
    resource_id: str | None,
    data_access_governed: bool | None,
    data_access_label_ids: Iterable[uuid.UUID | str] | None,
) -> tuple[bool, list[str]]:
    explicit_labels = (
        _normalize_label_ids(data_access_label_ids)
        if data_access_label_ids is not None
        else None
    )
    if data_access_governed is False and explicit_labels:
        raise ValueError("Ungoverned audit records cannot carry handling labels.")
    if data_access_governed is not None or explicit_labels is not None:
        return bool(data_access_governed or explicit_labels is not None), (
            explicit_labels or []
        )

    from app.services.audit_data_access import resolve_audit_data_access_labels

    resolved = resolve_audit_data_access_labels(
        db,
        resource_type=resource_type,
        resource_id=resource_id,
    )
    if resolved is None:
        return False, []
    return True, _normalize_label_ids(resolved)


def _normalize_label_ids(
    values: Iterable[uuid.UUID | str],
) -> list[str]:
    normalized: set[str] = set()
    for value in values:
        try:
            normalized.add(str(uuid.UUID(str(value))))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "Audit handling-label snapshots require UUID values."
            ) from exc
    return sorted(normalized)


def _uuid_or_none(value: object) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None
