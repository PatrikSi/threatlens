"""Keep editorial report jobs away from workers predating publication gates."""

from app.core.worker_queues import QUEUE_AI_REPORTS, QUEUE_AI_REPORTS_EDITORIAL
from app.models.report import Report


def report_worker_queue(report: Report) -> str:
    return (
        QUEUE_AI_REPORTS
        if report.editorial_contract_version == 0
        else QUEUE_AI_REPORTS_EDITORIAL
    )


def report_retry_queue(task) -> str:
    """A retry retains its delivery queue; unknown callers use the new contract."""
    delivery = getattr(getattr(task, "request", None), "delivery_info", None)
    queue = delivery.get("routing_key") if isinstance(delivery, dict) else None
    return (
        queue
        if queue in {QUEUE_AI_REPORTS, QUEUE_AI_REPORTS_EDITORIAL}
        else QUEUE_AI_REPORTS_EDITORIAL
    )
