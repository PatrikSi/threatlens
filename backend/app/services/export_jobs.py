"""Durable acceptance, bounded admission, and repair of export job state."""
import hashlib
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, exists, func, or_, select, text

from app.core.config import get_settings
from app.models.export_job import ExportJob, ExportJobChunk
from app.models.service_account import ServiceAccount
from app.models.user import User
from app.schemas.exports import ArticleExportJobResponse, ArticleExportRequest
from app.services.export_job_access import (
    ExportJobAccessDenied, assert_export_sources_visible, authorize_export_job,
    capture_export_authorization,
)
from app.services.secret_storage import encrypt_json

ACTIVE = ("queued", "running")
ERROR_MESSAGES = {
    "authorization_changed": "Access changed or the accepting credential expired. Start a new export with your current access.",
    "size_limit": "The export exceeds its item or byte budget. Narrow the filters or exclude article text.",
    "snapshot_changed": "Selected articles changed during generation. Start a new export.",
    "empty_export": "No articles matched when generation started.",
    "generation_failed": "Export generation failed. Start a new export or contact an administrator.",
    "generation_timeout": "Export generation exceeded its configured duration. Narrow the filters and try again.",
    "worker_interrupted": "The worker stopped repeatedly before completing this export. Start a new export.",
    "coordination_unavailable": "Export coordination is temporarily unavailable; the job will retry.",
    "owner_deleted": "The export owner no longer exists.",
    "artifact_unavailable": "The stored export is unavailable. Start a new export.",
}


class ExportJobConflict(RuntimeError):
    pass


class ExportJobCapacityExceeded(RuntimeError):
    pass


def create_export_job(db, *, principal, request, authorization, data_access, payload):
    settings = get_settings()
    principal_type = "user" if isinstance(principal, User) else "service_account"
    document = ArticleExportRequest.model_validate(payload.model_dump(exclude={"idempotency_key"})).model_dump(mode="json")
    digest = hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    snapshot = capture_export_authorization(request, authorization, data_access)
    # One short admission transaction protects the aggregate reservation budget.
    db.execute(text("SELECT pg_advisory_xact_lock(1952805744, 8701)"))
    prior = db.scalar(select(ExportJob).where(
        ExportJob.principal_type == principal_type, ExportJob.principal_id == principal.id,
        ExportJob.idempotency_key == payload.idempotency_key,
    ))
    if prior is not None:
        if prior.request_hash != digest:
            raise ExportJobConflict("This idempotency key belongs to a different export request")
        return prior, False
    count, reserved = db.execute(select(func.count(), func.coalesce(func.sum(ExportJob.reserved_bytes), 0))).one()
    active = db.scalar(select(func.count()).select_from(ExportJob).where(
        ExportJob.principal_type == principal_type, ExportJob.principal_id == principal.id,
        ExportJob.status.in_(ACTIVE),
    ))
    # Includes base64 + Fernet expansion, bounded row overhead and encrypted input.
    reservation = settings.export_max_uncompressed_bytes * 3 + 1_048_576
    if (count >= settings.export_job_max_retained
            or active >= settings.export_job_max_active_per_principal
            or reserved + reservation > settings.export_job_max_reserved_bytes):
        raise ExportJobCapacityExceeded("Background export capacity is full. Wait for existing jobs to finish or expire.")
    job = ExportJob(
        principal_type=principal_type, principal_id=principal.id,
        idempotency_key=payload.idempotency_key, request_hash=digest,
        request_encrypted=encrypt_json(document), authorization_encrypted=encrypt_json(snapshot),
        format=payload.format, expires_at=datetime.now(timezone.utc) + timedelta(seconds=settings.export_job_retention_seconds),
        reserved_bytes=reservation,
    )
    db.add(job)
    db.flush()
    return job, True


def clear_export_job_artifact(db, job):
    db.execute(delete(ExportJobChunk).where(ExportJobChunk.job_id == job.id))
    job.reserved_bytes = 0
    job.source_encrypted = None
    job.filename = None
    job.media_type = None
    job.file_size = None
    job.item_count = None
    job.completed_items = 0


def terminal_export_job(db, job, status, error_code=None):
    clear_export_job_artifact(db, job)
    job.status = status
    job.error_code = error_code
    job.claim_token = None
    job.lease_expires_at = None
    job.completed_at = datetime.now(timezone.utc)


def export_job_response(db, job, *, current_access=None):
    permitted = False
    try:
        _authorization, access = authorize_export_job(db, job)
        assert_export_sources_visible(db, job, access)
        if current_access is not None:
            assert_export_sources_visible(db, job, current_access)
        permitted = True
    except ExportJobAccessDenied:
        pass
    expired = job.expires_at <= datetime.now(timezone.utc)
    error = job.error_code if permitted else "authorization_changed"
    return ArticleExportJobResponse(
        id=job.id, format=job.format, status="expired" if expired else job.status,
        created_at=job.created_at, expires_at=job.expires_at,
        started_at=job.started_at, completed_at=job.completed_at, attempts=job.attempts,
        completed_items=job.completed_items if permitted else 0,
        item_count=job.item_count if permitted else None,
        file_size=job.file_size if permitted else None, filename=job.filename if permitted else None,
        error_code=error, message=ERROR_MESSAGES.get(error),
        download_available=permitted and not expired and job.status == "ready",
    )


def maintain_export_jobs(db, *, limit=50):
    now = datetime.now(timezone.utc)
    owner_missing = or_(
        (ExportJob.principal_type == "user") & ~exists().where(User.id == ExportJob.principal_id),
        (ExportJob.principal_type == "service_account") & ~exists().where(ServiceAccount.id == ExportJob.principal_id),
    )
    jobs = db.scalars(select(ExportJob).where(or_(
        (ExportJob.expires_at <= now) & (ExportJob.status != "expired"),
        owner_missing,
        (ExportJob.status == "running") & (ExportJob.lease_expires_at <= now),
    )).order_by(ExportJob.expires_at, ExportJob.id).limit(limit).with_for_update(skip_locked=True)).all()
    repaired = 0
    for job in jobs:
        model = User if job.principal_type == "user" else ServiceAccount
        if db.get(model, job.principal_id) is None:
            db.delete(job)
        elif job.expires_at <= now:
            terminal_export_job(db, job, "expired")
        elif job.attempts >= get_settings().export_job_max_attempts:
            terminal_export_job(db, job, "failed", "worker_interrupted")
        else:
            db.execute(delete(ExportJobChunk).where(ExportJobChunk.job_id == job.id))
            job.status = "queued"
            job.claim_token = None
            job.lease_expires_at = None
            job.next_attempt_at = now
            job.completed_items = 0
            job.source_encrypted = None
        repaired += 1
    # Keep a one-day tombstone after expiry for idempotent retries, then bound metadata too.
    old_ids = select(ExportJob.id).where(ExportJob.expires_at <= now - timedelta(days=1)).order_by(ExportJob.expires_at).limit(limit)
    db.execute(delete(ExportJob).where(ExportJob.id.in_(old_ids)))
    return repaired
