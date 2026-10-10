import base64

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.export_job import ExportJob, ExportJobChunk
from app.services.authorization import (
    AuthorizationContext,
    AuthorizationStateUnavailable,
    fence_authorization_context,
)
from app.services.data_access_policy import DataAccessContext, fence_data_access_context
from app.services.export_download_scratch import ExportDownloadScratch
from app.services.export_download_budget import ExportDownloadPreparationBudget
from app.services.export_job_access import (
    assert_export_sources_visible,
    authorize_export_job,
    fence_export_job_access,
)
from app.services.export_job_contracts import EXPORT_CHUNK_BYTES
from app.services.secret_storage import decrypt_text


class ExportJobArtifactUnavailable(RuntimeError):
    pass


def materialize_export_job_download(
    db: Session,
    job: ExportJob,
    *,
    current_authorization: AuthorizationContext | None,
    current_access: DataAccessContext,
    budget: ExportDownloadPreparationBudget,
) -> ExportDownloadScratch:
    budget.checkpoint()
    if current_authorization is None:
        raise AuthorizationStateUnavailable(
            "Current export authorization is unavailable"
        )
    authorization, access = authorize_export_job(db, job)
    assert_export_sources_visible(db, job, access)
    assert_export_sources_visible(db, job, current_access)
    if job.file_size is None or not 0 < job.file_size <= get_settings().export_max_uncompressed_bytes:
        raise ExportJobArtifactUnavailable("Stored artifact has an invalid size")
    budget.checkpoint()
    scratch = ExportDownloadScratch(reserved_bytes=job.file_size)
    total = 0
    position = 0
    try:
        while True:
            budget.checkpoint()
            ciphertext = db.scalar(
                select(ExportJobChunk.ciphertext).where(
                    ExportJobChunk.job_id == job.id,
                    ExportJobChunk.position == position,
                )
            )
            if ciphertext is None:
                break
            chunk = base64.b64decode(decrypt_text(ciphertext), validate=True)
            budget.checkpoint()
            if len(chunk) > EXPORT_CHUNK_BYTES:
                raise ExportJobArtifactUnavailable("Invalid artifact chunk")
            total += len(chunk)
            if total > job.file_size:
                raise ExportJobArtifactUnavailable(
                    "Stored artifact exceeds its reserved size"
                )
            scratch.file.write(chunk)
            budget.checkpoint()
            position += 1
        if total != job.file_size or total == 0:
            raise ExportJobArtifactUnavailable("Stored artifact is incomplete")
        fence_export_job_access(db, job, authorization, access)
        fence_authorization_context(db, current_authorization)
        fence_data_access_context(db, current_access)
        assert_export_sources_visible(db, job, current_access)
        scratch.file.flush()
        budget.checkpoint()
        return scratch
    except BaseException:
        scratch.close()
        raise
