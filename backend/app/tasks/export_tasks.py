import logging
import uuid
from datetime import datetime
from app.core.config import get_settings
from app.core.worker_queues import QUEUE_EXPORTS
from app.db import session as session_module
from app.db.budgets import database_operation
from app.services.export_job_dispatch import reserve_export_publications
from app.services.export_job_worker import execute_export_job
from app.services.export_jobs import maintain_export_jobs
from app.services.queue_execution_canaries import read_queue_execution_canaries
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def _export_consumer_progress() -> datetime | None:
    canary = read_queue_execution_canaries(settings=get_settings(), queues=[QUEUE_EXPORTS]).get(QUEUE_EXPORTS)
    return canary.heartbeat_at if canary is not None and canary.reason == "fresh" else None


def enqueue_export_job(job_id: uuid.UUID) -> bool:
    try:
        canary_at = _export_consumer_progress()
        with session_module.SessionLocal() as db, database_operation(db, operation="interactive"):
            identities = reserve_export_publications(db, job_id=job_id, canary_at=canary_at, limit=1)
            db.commit()
    except Exception as exc:
        logger.warning("export_job_reservation_deferred job_id=%s error_type=%s", job_id, type(exc).__name__)
        return False
    return bool(identities) and _publish_export_job(job_id)


def _publish_export_job(job_id: uuid.UUID) -> bool:
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
    with session_module.SessionLocal() as db, database_operation(db, operation="repair"):
        repaired = maintain_export_jobs(db)
        db.commit()
    canary_at = _export_consumer_progress()
    with session_module.SessionLocal() as db, database_operation(db, operation="repair"):
        ids = reserve_export_publications(db, canary_at=canary_at)
        db.commit()
    queued = sum(_publish_export_job(job_id) for job_id in ids)
    return {"queued": queued, "repaired": repaired}
