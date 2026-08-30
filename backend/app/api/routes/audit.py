import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.api.deps import (
    get_admin_user,
    get_auth_credential_kind,
    get_current_auth_session_id,
    require_token_scopes,
    resolve_client_ip,
)
from app.core.token_scopes import SCOPE_READ_AUDIT
from app.db.session import get_db
from app.models.audit_log import AuditLog
from app.models.user import User
from app.schemas.audit import (
    AuditLogExportResponse,
    AuditLogListResponse,
    AuditLogResponse,
)
from app.services.audit import record_audit

router = APIRouter(prefix="/audit-logs", tags=["audit"])


def _build_audit_query(
    *,
    action: str | None,
    actor_user_id: uuid.UUID | None,
    actor_principal_type: str | None,
    actor_principal_id: uuid.UUID | None,
    resource_type: str | None,
    resource_id: str | None,
    request_id: str | None,
    success: bool | None,
    created_from: datetime | None,
    created_to: datetime | None,
):
    query = select(AuditLog)
    filters = []
    if action:
        filters.append(AuditLog.action == action)
    if actor_user_id:
        filters.append(AuditLog.actor_user_id == actor_user_id)
    if actor_principal_type:
        filters.append(AuditLog.actor_principal_type == actor_principal_type)
    if actor_principal_id:
        filters.append(AuditLog.actor_principal_id == actor_principal_id)
    if resource_type:
        filters.append(AuditLog.resource_type == resource_type)
    if resource_id:
        filters.append(AuditLog.resource_id == resource_id)
    if request_id:
        filters.append(AuditLog.request_id == request_id)
    if success is not None:
        filters.append(AuditLog.success.is_(success))
    if created_from:
        filters.append(AuditLog.created_at >= created_from)
    if created_to:
        filters.append(AuditLog.created_at <= created_to)

    if filters:
        query = query.where(and_(*filters))
    return query


@router.get("", response_model=AuditLogListResponse)
def list_audit_logs(
    action: str | None = Query(default=None, max_length=255),
    actor_user_id: uuid.UUID | None = None,
    actor_principal_type: str | None = Query(default=None, max_length=32),
    actor_principal_id: uuid.UUID | None = None,
    resource_type: str | None = Query(default=None, max_length=255),
    resource_id: str | None = Query(default=None, max_length=255),
    request_id: str | None = Query(default=None, max_length=128),
    success: bool | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_READ_AUDIT)),
):
    _ = admin
    query = _build_audit_query(
        action=action,
        actor_user_id=actor_user_id,
        actor_principal_type=actor_principal_type,
        actor_principal_id=actor_principal_id,
        resource_type=resource_type,
        resource_id=resource_id,
        request_id=request_id,
        success=success,
        created_from=created_from,
        created_to=created_to,
    )

    count_stmt = select(func.count()).select_from(query.subquery())
    total = db.scalar(count_stmt) or 0

    rows = db.scalars(
        query.order_by(AuditLog.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    return AuditLogListResponse(
        logs=[AuditLogResponse.model_validate(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/export", response_model=AuditLogExportResponse)
def export_audit_logs(
    request: Request,
    action: str | None = Query(default=None, max_length=255),
    actor_user_id: uuid.UUID | None = None,
    actor_principal_type: str | None = Query(default=None, max_length=32),
    actor_principal_id: uuid.UUID | None = None,
    resource_type: str | None = Query(default=None, max_length=255),
    resource_id: str | None = Query(default=None, max_length=255),
    request_id: str | None = Query(default=None, max_length=128),
    success: bool | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    limit: int = Query(default=5000, ge=1, le=20000),
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_READ_AUDIT)),
):
    query = _build_audit_query(
        action=action,
        actor_user_id=actor_user_id,
        actor_principal_type=actor_principal_type,
        actor_principal_id=actor_principal_id,
        resource_type=resource_type,
        resource_id=resource_id,
        request_id=request_id,
        success=success,
        created_from=created_from,
        created_to=created_to,
    )

    count_stmt = select(func.count()).select_from(query.subquery())
    total = int(db.scalar(count_stmt) or 0)
    rows = db.scalars(query.order_by(AuditLog.created_at.desc()).limit(limit)).all()

    response = AuditLogExportResponse(
        exported_at=datetime.now(timezone.utc),
        total=total,
        truncated=total > limit,
        logs=[AuditLogResponse.model_validate(row) for row in rows],
    )
    credential_id = getattr(request.state, "api_token_id", None)
    if credential_id is None:
        credential_id = get_current_auth_session_id(request)
    record_audit(
        db,
        actor_user_id=admin.id,
        actor_principal_type="user",
        actor_principal_id=admin.id,
        credential_kind=get_auth_credential_kind(request),
        credential_id=credential_id,
        request_id=getattr(request.state, "request_id", None),
        source_ip=resolve_client_ip(request),
        action="audit.export",
        resource_type="audit_log",
        metadata={
            "filters": {
                "action": action,
                "actor_user_id": str(actor_user_id) if actor_user_id else None,
                "actor_principal_type": actor_principal_type,
                "actor_principal_id": (
                    str(actor_principal_id) if actor_principal_id else None
                ),
                "resource_type": resource_type,
                "resource_id": resource_id,
                "request_id": request_id,
                "success": success,
                "created_from": created_from.isoformat() if created_from else None,
                "created_to": created_to.isoformat() if created_to else None,
            },
            "exported_count": len(response.logs),
            "matching_count": response.total,
            "truncated": response.truncated,
        },
    )
    db.commit()
    return response
