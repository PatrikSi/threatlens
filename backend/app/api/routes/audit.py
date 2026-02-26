import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.api.deps import get_admin_user, require_token_scopes
from app.core.token_scopes import SCOPE_READ_AUDIT
from app.db.session import get_db
from app.models.audit_log import AuditLog
from app.models.user import User
from app.schemas.audit import AuditLogExportResponse, AuditLogListResponse, AuditLogResponse

router = APIRouter(prefix="/audit-logs", tags=["audit"])


def _build_audit_query(action: str | None, actor_user_id: uuid.UUID | None):
    query = select(AuditLog)
    filters = []
    if action:
        filters.append(AuditLog.action == action)
    if actor_user_id:
        filters.append(AuditLog.actor_user_id == actor_user_id)

    if filters:
        query = query.where(and_(*filters))
    return query


@router.get("", response_model=AuditLogListResponse)
def list_audit_logs(
    action: str | None = None,
    actor_user_id: uuid.UUID | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_READ_AUDIT)),
):
    _ = admin
    query = _build_audit_query(action, actor_user_id)

    count_stmt = select(func.count()).select_from(query.subquery())
    total = db.scalar(count_stmt) or 0

    rows = db.scalars(query.order_by(AuditLog.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()

    return AuditLogListResponse(
        logs=[AuditLogResponse.model_validate(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/export", response_model=AuditLogExportResponse)
def export_audit_logs(
    action: str | None = None,
    actor_user_id: uuid.UUID | None = None,
    limit: int = Query(default=5000, ge=1, le=20000),
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_READ_AUDIT)),
):
    _ = admin
    query = _build_audit_query(action, actor_user_id)

    count_stmt = select(func.count()).select_from(query.subquery())
    total = int(db.scalar(count_stmt) or 0)
    rows = db.scalars(query.order_by(AuditLog.created_at.desc()).limit(limit)).all()

    return AuditLogExportResponse(
        exported_at=datetime.now(timezone.utc),
        total=total,
        truncated=total > limit,
        logs=[AuditLogResponse.model_validate(row) for row in rows],
    )
