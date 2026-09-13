"""Current and accepting authority for repeatable processing recovery."""

from sqlalchemy.orm import Session

from app.core.token_scopes import SCOPE_READ_ITEMS, SCOPE_WRITE_OPERATIONS
from app.models.processing_work import ProcessingRecoveryRun
from app.services.export_job_access import (
    assert_export_sources_visible,
    authorize_export_job,
    fence_export_authorization,
)

RECOVERY_PERMISSIONS = (SCOPE_READ_ITEMS, SCOPE_WRITE_OPERATIONS)


def authorize_recovery_run(
    db: Session, run: ProcessingRecoveryRun, *, fence: bool = False
):
    authorization, access = authorize_export_job(
        db, run, required_permissions=RECOVERY_PERMISSIONS
    )
    if fence:
        fence_export_authorization(
            db, run, authorization, access, required_permissions=RECOVERY_PERMISSIONS
        )
    assert_export_sources_visible(db, run, access)
    return authorization, access
