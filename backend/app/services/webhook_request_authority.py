"""Fence personal webhook administration and evidence reads through their credential."""

from dataclasses import dataclass, field, replace
import uuid

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services.authorization import AuthorizationContext
from app.services.authorization import fence_authorization_context
from app.services.data_access_policy import DataAccessContext
from app.services.data_access_policy import fence_data_access_context
from app.models.user import User
from app.services.export_job_access import (
    ExportJobAccessDenied,
    capture_export_authorization,
    fence_export_authorization,
    authorize_export_job,
)
from app.services.export_job_contracts import ExportAuthorizationSnapshot


@dataclass
class _RequestPrincipal:
    principal_id: uuid.UUID
    principal_type: str = "user"
    authorization_encrypted: dict = field(default_factory=dict)
    source_encrypted: dict | None = None


def reauthorize_webhook_test_credential(
    db: Session,
    *,
    authorization: AuthorizationContext,
    data_access: DataAccessContext,
    snapshot: ExportAuthorizationSnapshot,
    required_permissions: tuple[str, ...] = ("write:notifications",),
) -> tuple[AuthorizationContext, DataAccessContext]:
    """Recheck the accepting credential after policy fences and before source locks."""
    current_authorization, current_access = authorize_export_job(
        db,
        _RequestPrincipal(authorization.principal_id, authorization.principal_type),
        lock=True,
        snapshot=snapshot,
        required_permissions=required_permissions,
    )
    if current_authorization.policy_revision != authorization.policy_revision:
        raise ExportJobAccessDenied("Webhook test authorization changed")
    return current_authorization, replace(
        data_access,
        principal_eligible=data_access.principal_eligible
        and current_access.principal_eligible,
        allowed_label_ids=data_access.allowed_label_ids
        & current_access.allowed_label_ids,
    )


def fence_webhook_request(
    db: Session,
    *,
    request: Request,
    authorization: AuthorizationContext,
    data_access: DataAccessContext,
    permission: str,
    exclusive_owner: bool = False,
    additional_permissions: tuple[str, ...] = (),
) -> None:
    """Use the common policy→owner→credential ordering; retain locks until response/commit."""
    try:
        if exclusive_owner:
            # Acquire the final lock mode first; simultaneous SHARE→UPDATE
            # upgrades would deadlock concurrent profile-capacity admission.
            fence_authorization_context(db, authorization)
            fence_data_access_context(db, data_access)
            db.scalar(
                select(User.id)
                .where(User.id == authorization.principal_id)
                .with_for_update()
            )
        snapshot = capture_export_authorization(request, authorization, data_access)
        fence_export_authorization(
            db,
            _RequestPrincipal(authorization.principal_id, authorization.principal_type),
            authorization,
            data_access,
            snapshot=snapshot,
            required_permissions=(permission, *additional_permissions),
        )
    except ExportJobAccessDenied as exc:
        raise HTTPException(
            403,
            "Your credentials or webhook access changed; refresh your session before retrying",
        ) from exc
