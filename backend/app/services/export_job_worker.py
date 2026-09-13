"""Lease-fenced, restartable generation of bounded background export artifacts."""

from __future__ import annotations

import base64
import logging
import shutil
import threading
import time
import uuid
from contextlib import contextmanager
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from billiard.exceptions import SoftTimeLimitExceeded

from app.core.config import get_settings
from app.db import session as session_module
from app.models.export_job import ExportJob, ExportJobChunk
from app.schemas.exports import ArticleExportRequest
from app.services.authorization import AuthorizationStateUnavailable
from app.services.data_access_policy import DataPolicyError
from app.services.export_artifacts import (
    ExportArtifact,
    ExportSizeLimitError,
    generate_export_artifact,
    remove_export_artifact,
)
from app.services.export_job_access import (
    ExportJobAccessDenied,
    authorize_export_job,
    export_source_snapshot,
    fence_export_job_access,
)
from app.services.export_jobs import (
    retained_export_job_reservation,
    terminal_export_job,
)
from app.services.export_job_contracts import (
    EXPORT_CHUNK_BYTES,
    ExportCheckpoint,
    ExportExecutionResult,
)
from app.services.export_job_dispatch import reset_export_publication
from app.services.export_models import ExportRecord
from app.services.export_job_scratch import (
    clean_local_export_scratch,
    export_scratch_directory,
)
from app.services.export_lock import (
    ExportAlreadyRunningError,
    ExportLockUnavailableError,
    acquire_export_lock,
)
from app.services.export_query import (
    ExportAuthorizationChangedError,
    ExportSnapshotChangedError,
    ExportTextProjection,
    build_export_query_context,
    iter_export_records,
    load_export_item_ids,
)
from app.services.secret_storage import decrypt_json, encrypt_json, encrypt_text

logger = logging.getLogger(__name__)


class ExportJobInterrupted(RuntimeError):
    pass


class ExportJobTimedOut(RuntimeError):
    pass


class ExportJobEmpty(RuntimeError):
    pass


def _owned_job(db: Session, job_id: uuid.UUID, token: uuid.UUID) -> ExportJob:
    job = db.scalar(select(ExportJob).where(ExportJob.id == job_id).with_for_update())
    now = datetime.now(timezone.utc)
    if (
        job is None
        or job.status != "running"
        or job.claim_token != token
        or job.lease_expires_at <= now
        or job.expires_at <= now
    ):
        raise ExportJobInterrupted("Export job claim is no longer current")
    return job


def claim_export_job(job_id: uuid.UUID) -> uuid.UUID | None:
    settings = get_settings()
    with session_module.SessionLocal() as db:
        job = db.scalar(
            select(ExportJob)
            .where(ExportJob.id == job_id)
            .with_for_update(skip_locked=True)
        )
        now = datetime.now(timezone.utc)
        if job is None or job.status != "queued" or job.next_attempt_at > now:
            return None
        if job.expires_at <= now:
            terminal_export_job(db, job, "expired")
            db.commit()
            return None
        if job.attempts >= settings.export_job_max_attempts:
            terminal_export_job(db, job, "failed", "worker_interrupted")
            db.commit()
            return None
        token = uuid.uuid4()
        job.status = "running"
        job.claim_token = token
        job.lease_expires_at = now + timedelta(
            seconds=settings.export_job_lease_seconds
        )
        job.started_at = now
        job.attempts += 1
        job.error_code = None
        job.completed_items = 0
        db.execute(delete(ExportJobChunk).where(ExportJobChunk.job_id == job.id))
        db.commit()
        return token


@contextmanager
def _renewing_claim(job_id: uuid.UUID, token: uuid.UUID) -> Iterator[threading.Event]:
    stop = threading.Event()
    lost = threading.Event()
    settings = get_settings()

    def renew() -> None:
        while not stop.wait(min(10, settings.export_job_lease_seconds / 3)):
            try:
                with session_module.SessionLocal() as db:
                    job = _owned_job(db, job_id, token)
                    job.lease_expires_at = datetime.now(timezone.utc) + timedelta(
                        seconds=settings.export_job_lease_seconds
                    )
                    db.commit()
            except Exception:
                lost.set()
                return

    thread = threading.Thread(
        target=renew, name=f"export-job-lease:{job_id}", daemon=True
    )
    thread.start()
    try:
        yield lost
    finally:
        stop.set()
        thread.join(timeout=1)


def execute_export_job(job_id: uuid.UUID) -> ExportExecutionResult:
    clean_local_export_scratch()
    token = claim_export_job(job_id)
    if token is None:
        return {"status": "skipped"}
    artifact = None
    scratch = None
    started = time.monotonic()
    try:
        with _renewing_claim(job_id, token) as lost:
            scratch = export_scratch_directory(job_id, token)
            checkpoint = _checkpoint(job_id, token, started, lost)
            with session_module.SessionLocal() as db:
                job = db.get(ExportJob, job_id)
                settings = get_settings()
                with acquire_export_lock(
                    settings=settings,
                    principal_type=job.principal_type,
                    principal_id=job.principal_id,
                ):
                    artifact = _generate(db, job, token, checkpoint, scratch)
                    _store_artifact(job_id, token, artifact, checkpoint)
                    checkpoint(force=True)
                # Redis ownership is verified before publication; claim token
                # and fresh authorization protect publication after lock loss.
                _publish(job_id, token, artifact)
        return {"status": "ready", "job_id": str(job_id)}
    except ExportJobInterrupted:
        return {"status": "interrupted", "job_id": str(job_id)}
    except Exception as exc:
        _settle_failure(job_id, token, exc)
        logger.warning(
            "export_job_attempt_failed job_id=%s error_type=%s",
            job_id,
            type(exc).__name__,
        )
        return {"status": "failed", "job_id": str(job_id)}
    finally:
        if artifact is not None:
            remove_export_artifact(artifact.path)
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)


def _checkpoint(
    job_id: uuid.UUID,
    token: uuid.UUID,
    started: float,
    lost: threading.Event,
) -> ExportCheckpoint:
    last_checked = -float("inf")

    def check(*, completed: int | None = None, force: bool = False) -> None:
        nonlocal last_checked
        now = time.monotonic()
        if now - started >= get_settings().export_job_timeout_seconds:
            raise ExportJobTimedOut("Export job generation deadline exceeded")
        if lost.is_set():
            raise ExportJobInterrupted("Export job lease could not be renewed")
        if not force and now - last_checked < 1:
            return
        with session_module.SessionLocal() as db:
            job = _owned_job(db, job_id, token)
            authorize_export_job(db, job)
            if completed is not None:
                job.completed_items = completed
            db.commit()
        last_checked = now

    return check


def _generate(
    db: Session,
    job: ExportJob,
    token: uuid.UUID,
    checkpoint: ExportCheckpoint,
    scratch: Path,
) -> ExportArtifact:
    settings = get_settings()
    payload = ArticleExportRequest.model_validate(decrypt_json(job.request_encrypted))
    _authorization, access = authorize_export_job(db, job)
    context = build_export_query_context(
        user_id=job.principal_id if job.principal_type == "user" else None,
        filters=payload.filters,
        data_access=access,
    )
    item_limit = (
        settings.export_pdf_max_items
        if payload.format == "pdf_bundle"
        else settings.export_max_items
    )
    item_ids = load_export_item_ids(db, context=context, limit=item_limit + 1)
    if not item_ids:
        raise ExportJobEmpty("No matching articles")
    if len(item_ids) > item_limit:
        raise ExportSizeLimitError("Export item limit exceeded")
    sources = export_source_snapshot(db, item_ids)
    with session_module.SessionLocal() as state_db:
        owned = _owned_job(state_db, job.id, token)
        owned.item_count = len(item_ids)
        owned.source_encrypted = encrypt_json(sources)
        state_db.commit()
    needs_text = {
        "stix": False,
        "csv": payload.options.csv_include_article_text,
        "pdf_bundle": payload.options.pdf_include_article_text,
    }.get(payload.format, payload.options.include_article_text)
    records = iter_export_records(
        db,
        item_ids=item_ids,
        context=context,
        include_iocs=payload.options.include_iocs,
        text_projection=ExportTextProjection(include_article_text=needs_text),
    )

    def checked_records() -> Iterator[ExportRecord]:
        for position, record in enumerate(records):
            checkpoint(completed=position)
            yield record
        checkpoint(completed=len(item_ids), force=True)

    return generate_export_artifact(
        checked_records(),
        item_count=len(item_ids),
        export_format=payload.format,
        filters=payload.filters,
        options=payload.options,
        max_uncompressed_bytes=settings.export_max_uncompressed_bytes,
        artifact_directory=scratch,
    )


def _store_artifact(
    job_id: uuid.UUID,
    token: uuid.UUID,
    artifact: ExportArtifact,
    checkpoint: ExportCheckpoint,
) -> None:
    if artifact.file_size > get_settings().export_max_uncompressed_bytes:
        raise ExportSizeLimitError("Stored artifact exceeds byte budget")
    stored = 0
    with artifact.path.open("rb") as source:
        position = 0
        while raw := source.read(EXPORT_CHUNK_BYTES):
            checkpoint(force=True)
            ciphertext = encrypt_text(base64.b64encode(raw).decode("ascii"))
            stored += len(ciphertext) + 128
            with session_module.SessionLocal() as db:
                job = _owned_job(db, job_id, token)
                if stored > job.reserved_bytes:
                    raise ExportSizeLimitError("Stored artifact exceeds reservation")
                db.add(
                    ExportJobChunk(
                        job_id=job_id, position=position, ciphertext=ciphertext
                    )
                )
                db.commit()
            position += 1


def _publish(job_id: uuid.UUID, token: uuid.UUID, artifact: ExportArtifact) -> None:
    with session_module.SessionLocal() as db:
        job = _owned_job(db, job_id, token)
        authorization, access = authorize_export_job(db, job)
        fence_export_job_access(db, job, authorization, access)
        retained = retained_export_job_reservation(db, job)
        if retained > job.reserved_bytes:
            raise ExportSizeLimitError("Retained artifact exceeds reservation")
        # Settlement only releases capacity. The same row lock and terminal
        # claim transition prevent another attempt from appending afterward.
        job.reserved_bytes = retained
        job.status = "ready"
        job.completed_at = datetime.now(timezone.utc)
        job.completed_items = artifact.item_count
        job.filename = artifact.filename
        job.media_type = artifact.media_type
        job.file_size = artifact.file_size
        job.claim_token = None
        job.lease_expires_at = None
        db.commit()


def _settle_failure(job_id: uuid.UUID, token: uuid.UUID, exc: Exception) -> None:
    with session_module.SessionLocal() as db:
        try:
            job = _owned_job(db, job_id, token)
        except ExportJobInterrupted:
            return
        if isinstance(
            exc,
            (
                ExportJobAccessDenied,
                AuthorizationStateUnavailable,
                DataPolicyError,
                ExportAuthorizationChangedError,
            ),
        ):
            reason = "authorization_changed"
        elif isinstance(exc, ExportSizeLimitError):
            reason = "size_limit"
        elif isinstance(exc, ExportSnapshotChangedError):
            reason = "snapshot_changed"
        elif isinstance(exc, ExportJobEmpty):
            reason = "empty_export"
        elif isinstance(exc, (ExportJobTimedOut, SoftTimeLimitExceeded)):
            from app.core.runtime_metrics import record_runtime_event

            record_runtime_event("export_generation_deadline")
            reason = "generation_timeout"
        else:
            reason = "generation_failed"
        retry = isinstance(
            exc, (ExportAlreadyRunningError, ExportLockUnavailableError, OSError)
        )
        if retry and (
            isinstance(exc, ExportAlreadyRunningError)
            or job.attempts < get_settings().export_job_max_attempts
        ):
            db.execute(delete(ExportJobChunk).where(ExportJobChunk.job_id == job.id))
            job.status = "queued"
            job.claim_token = None
            job.lease_expires_at = None
            job.next_attempt_at = datetime.now(timezone.utc) + timedelta(
                seconds=min(60, 10 * job.attempts)
            )
            reset_export_publication(job)
            job.error_code = "coordination_unavailable"
            if isinstance(exc, ExportAlreadyRunningError):
                job.attempts -= 1
        else:
            terminal_export_job(db, job, "failed", reason)
        db.commit()
