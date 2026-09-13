"""Principal-specific export options shared by synchronous and durable routes."""

import uuid

from app.core.api_errors import ApiHTTPException
from app.models.user import User
from app.schemas.exports import ArticleExportFilters
from app.services.export_job_contracts import ExportPrincipal, ExportPrincipalType


def export_user_id(principal: ExportPrincipal) -> uuid.UUID | None:
    return principal.id if isinstance(principal, User) else None


def export_principal_type(principal: ExportPrincipal) -> ExportPrincipalType:
    return "user" if isinstance(principal, User) else "service_account"


def require_supported_export_state(
    principal: ExportPrincipal,
    *,
    filters: ArticleExportFilters,
    include_user_state: bool = False,
) -> None:
    if isinstance(principal, User):
        return
    if (
        filters.is_read is None
        and filters.is_starred is None
        and not include_user_state
    ):
        return
    raise ApiHTTPException(
        status_code=400,
        detail=(
            "Service accounts do not have personal read, starred, or note state. "
            "Remove user-state filters and disable user-state export fields."
        ),
        error_code="service_account_user_state_unsupported",
    )
