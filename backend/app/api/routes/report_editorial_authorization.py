"""Fence a report command using the same credential contract as durable work."""

from dataclasses import dataclass, field
import uuid

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from app.api.routes.report_route_helpers import refence_report_context
from app.services.authorization import AuthorizationContext
from app.services.data_access_policy import DataAccessContext
from app.services.export_job_access import (
    ExportJobAccessDenied,
    capture_export_authorization,
    fence_export_authorization,
)


@dataclass
class _RequestPrincipal:
    """Adapter for the existing narrow CredentialBoundWork authority contract.

    The scope snapshot is passed explicitly; no secret is encrypted or persisted.
    """

    principal_id: uuid.UUID
    principal_type: str = "user"
    authorization_encrypted: dict = field(default_factory=dict)
    source_encrypted: dict | None = None


def fence_editorial_request(
    db: Session,
    *,
    request: Request,
    authorization: AuthorizationContext,
    data_access: DataAccessContext,
) -> None:
    _fence_report_request(db, request=request, authorization=authorization, data_access=data_access, permission="write:reports")


def fence_report_read_request(
    db: Session, *, request: Request, authorization: AuthorizationContext,
    data_access: DataAccessContext,
) -> None:
    _fence_report_request(db, request=request, authorization=authorization, data_access=data_access, permission="read:reports")


def _fence_report_request(
    db: Session, *, request: Request, authorization: AuthorizationContext,
    data_access: DataAccessContext, permission: str,
) -> None:
    # Global policies precede the user and credential, which precede Report.
    # Repeating this fence before commit also checks wall-clock credential and
    # temporary permission expiry without releasing the established locks.
    refence_report_context(db, authorization=authorization, data_access=data_access)
    try:
        snapshot = capture_export_authorization(request, authorization, data_access)
        fence_export_authorization(
            db,
            _RequestPrincipal(authorization.principal_id, principal_type=authorization.principal_type),
            authorization,
            data_access,
            snapshot=snapshot,
            required_permissions=(permission,),
        )
    except ExportJobAccessDenied as exc:
        raise HTTPException(
            status_code=403,
            detail="Your credentials or report access changed while this request was in progress. Refresh your session before trying again.",
        ) from exc
