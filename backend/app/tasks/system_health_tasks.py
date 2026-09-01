from datetime import datetime, timezone

from app.core.config import get_settings
from app.services.operations import collect_operations_overview
from app.services.operations_health_history import record_system_health_sample
from app.services.queue_execution_canaries import write_queue_execution_canary
from app.services.worker_health import collect_worker_topology
from app.tasks.celery_app import celery_app
from app.tasks.task_session import db_session


@celery_app.task(
    bind=True,
    name="app.tasks.system_health_tasks.record_queue_execution_canary",
    ignore_result=True,
)
def record_queue_execution_canary(self, expected_queue: str):
    delivery_info = getattr(self.request, "delivery_info", None)
    actual_queue = (
        delivery_info.get("routing_key")
        if isinstance(delivery_info, dict)
        else None
    )
    snapshot = write_queue_execution_canary(
        settings=get_settings(),
        expected_queue=str(expected_queue),
        actual_queue=str(actual_queue) if actual_queue else None,
        worker_name=getattr(self.request, "hostname", None),
    )
    return {
        "status": "ok" if snapshot.reason == "fresh" else "error",
        "queue": snapshot.queue,
        "reason": snapshot.reason,
    }


@celery_app.task(
    name="app.tasks.system_health_tasks.collect_system_health_sample",
    acks_late=True,
    reject_on_worker_lost=True,
    ignore_result=True,
)
def collect_system_health_sample():
    settings = get_settings()
    with db_session() as db:
        sampled_at = datetime.now(timezone.utc)
        topology = collect_worker_topology(settings, now=sampled_at)
        overview = collect_operations_overview(
            db,
            now=sampled_at,
            worker_topology=topology,
        )
        sample, created = record_system_health_sample(
            db,
            overview=overview,
            worker_topology=topology,
        )
    return {
        "status": "ok",
        "created": created,
        "sampled_at": sample.sampled_at.isoformat(),
        "overall_status": sample.overall_status,
        "worker_status": sample.worker_status,
        "worker_reason": sample.worker_reason,
    }
