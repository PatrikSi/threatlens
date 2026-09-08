import base64

from sqlalchemy import select

from app.core.config import get_settings
from app.models.export_job import ExportJobChunk
from app.services.export_download_scratch import ExportDownloadScratch
from app.services.export_job_access import (
    assert_export_sources_visible, authorize_export_job, fence_export_job_access,
)
from app.services.export_job_worker import CHUNK_BYTES
from app.services.secret_storage import decrypt_text


class ExportJobArtifactUnavailable(RuntimeError):
    pass


def materialize_export_job_download(db, job, *, current_authorization, current_access):
    from app.services.authorization import fence_authorization_context
    from app.services.data_access_policy import fence_data_access_context

    authorization, access = authorize_export_job(db, job)
    assert_export_sources_visible(db, job, access)
    assert_export_sources_visible(db, job, current_access)
    scratch = ExportDownloadScratch()
    total = 0
    position = 0
    try:
        while True:
            ciphertext = db.scalar(select(ExportJobChunk.ciphertext).where(
                ExportJobChunk.job_id == job.id, ExportJobChunk.position == position,
            ))
            if ciphertext is None:
                break
            chunk = base64.b64decode(decrypt_text(ciphertext), validate=True)
            if len(chunk) > CHUNK_BYTES:
                raise ExportJobArtifactUnavailable("Invalid artifact chunk")
            total += len(chunk)
            if total > get_settings().export_max_uncompressed_bytes:
                raise ExportJobArtifactUnavailable("Stored artifact exceeds the current byte cap")
            scratch.file.write(chunk)
            position += 1
        if total != job.file_size or total == 0:
            raise ExportJobArtifactUnavailable("Stored artifact is incomplete")
        fence_export_job_access(db, job, authorization, access)
        fence_authorization_context(db, current_authorization)
        fence_data_access_context(db, current_access)
        assert_export_sources_visible(db, job, current_access)
        return scratch
    except BaseException:
        scratch.close()
        raise
