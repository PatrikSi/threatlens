import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.config import get_settings
from app.db import session as session_module
from app.models.export_job import ExportJob
from app.services.export_job_worker import execute_export_job
from app.services.export_jobs import maintain_export_jobs
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def enqueue_export_job(job_id):
    settings = get_settings()
    try:
        # Durable dispatch owns retries. A dedicated producer avoids changing
        # worker BRPOP behavior or waiting on a pooled publisher connection.
        options = dict(celery_app.conf.broker_transport_options or {})
        options.update(socket_connect_timeout=settings.redis_connect_timeout_seconds,
                       socket_timeout=settings.redis_socket_timeout_seconds,
                       max_retries=0)
        with celery_app.connection_for_write(
            connect_timeout=settings.redis_connect_timeout_seconds,
            transport_options=options,
        ) as connection:
            generate_export_job.apply_async(
                args=[str(job_id)], connection=connection, retry=False, ignore_result=True,
                soft_time_limit=settings.export_job_timeout_seconds,
                time_limit=settings.export_job_timeout_seconds + 60,
            )
        return True
    except Exception as exc:
        logger.warning("export_job_publish_deferred job_id=%s error_type=%s", job_id, type(exc).__name__)
        return False


@celery_app.task(name="app.tasks.export_tasks.generate_export_job", acks_late=True, reject_on_worker_lost=True, soft_time_limit=3600, time_limit=3660)
def generate_export_job(job_id: str):
    return execute_export_job(uuid.UUID(job_id))


@celery_app.task(name="app.tasks.export_tasks.dispatch_export_jobs")
def dispatch_export_jobs():
    with session_module.SessionLocal() as db:
        repaired = maintain_export_jobs(db)
        db.commit()
        now = datetime.now(timezone.utc)
        jobs = db.scalars(select(ExportJob).where(
            ExportJob.status == "queued", ExportJob.expires_at > now,
            ExportJob.next_attempt_at <= now, ExportJob.next_dispatch_at <= now,
        ).order_by(ExportJob.created_at, ExportJob.id).limit(25).with_for_update(skip_locked=True)).all()
        ids = [job.id for job in jobs]
        # A publication lease limits duplicate broker messages; the generation
        # claim is independent, so a crash here is repaired on the next sweep.
        for job in jobs:
            job.next_dispatch_at = now + timedelta(seconds=30)
        db.commit()
    queued = sum(enqueue_export_job(job_id) for job_id in ids)
    return {"queued": queued, "repaired": repaired}
