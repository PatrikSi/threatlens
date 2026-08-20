import logging
import time

from celery import Celery
from celery.schedules import crontab
from celery.signals import setup_logging, task_postrun, task_prerun
from kombu import Queue

from app.core.config import get_settings
from app.core.logging_config import (
    configure_logging,
    log_configuration_summary,
    reset_log_context,
    set_log_context,
    verbose_logging_enabled,
)

settings = get_settings()
logger = logging.getLogger("threatlens.worker")
_TASK_CONTEXT_TOKEN_ATTRIBUTE = "_threatlens_log_context_token"
_TASK_STARTED_AT_ATTRIBUTE = "_threatlens_task_started_at"


@setup_logging.connect
def configure_celery_logging(**_kwargs) -> None:
    configure_logging(settings)
    log_configuration_summary(settings)


@task_prerun.connect
def add_task_log_context(
    *,
    task_id: str | None = None,
    task=None,
    args=None,
    kwargs=None,
    **_signal_kwargs,
) -> None:
    if task is None:
        return

    task_name = str(getattr(task, "name", None) or type(task).__name__)
    request = getattr(task, "request", None)
    queue = _task_queue(request)
    token = set_log_context(task_id=task_id, task_name=task_name)
    if request is not None:
        setattr(request, _TASK_CONTEXT_TOKEN_ATTRIBUTE, token)
        setattr(request, _TASK_STARTED_AT_ATTRIBUTE, time.perf_counter())

    if verbose_logging_enabled(settings):
        logger.debug(
            "task_started positional_arg_count=%s keyword_keys=%s",
            len(args or ()),
            sorted(str(key) for key in (kwargs or {}).keys()),
            extra={"task_id": task_id, "task_name": task_name, "queue": queue},
        )


@task_postrun.connect
def complete_task_log_context(
    *,
    task_id: str | None = None,
    task=None,
    state: str | None = None,
    **_signal_kwargs,
) -> None:
    if task is None:
        return

    request = getattr(task, "request", None)
    started_at = getattr(request, _TASK_STARTED_AT_ATTRIBUTE, None)
    duration_ms = (time.perf_counter() - started_at) * 1000 if isinstance(started_at, (int, float)) else None
    task_name = str(getattr(task, "name", None) or type(task).__name__)
    if verbose_logging_enabled(settings):
        logger.debug(
            "task_complete state=%s",
            state or "unknown",
            extra={
                "task_id": task_id,
                "task_name": task_name,
                "queue": _task_queue(request),
                "duration_ms": round(duration_ms, 2) if duration_ms is not None else None,
            },
        )

    token = getattr(request, _TASK_CONTEXT_TOKEN_ATTRIBUTE, None)
    try:
        if token is not None:
            reset_log_context(token)
    finally:
        for attribute in (_TASK_CONTEXT_TOKEN_ATTRIBUTE, _TASK_STARTED_AT_ATTRIBUTE):
            if request is not None and hasattr(request, attribute):
                delattr(request, attribute)


def _task_queue(request) -> str | None:
    delivery_info = getattr(request, "delivery_info", None)
    if not isinstance(delivery_info, dict):
        return None
    queue = delivery_info.get("routing_key") or delivery_info.get("exchange")
    return str(queue) if queue else None

QUEUE_DEFAULT = "default"
QUEUE_INGEST = "ingest"
QUEUE_PROCESSING = "processing"
QUEUE_NOTIFICATIONS = "notifications"
QUEUE_AI = "ai"
QUEUE_MAINTENANCE = "maintenance"

TASK_ROUTES = {
    "app.tasks.feed_tasks.fetch_feed": {"queue": QUEUE_INGEST},
    "app.tasks.feed_tasks.backfill_feed_metadata": {"queue": QUEUE_INGEST},
    "app.tasks.feed_tasks.dispatch_due_feeds": {"queue": QUEUE_MAINTENANCE},
    "app.tasks.feed_tasks.dispatch_feed_metadata_backfill": {"queue": QUEUE_MAINTENANCE},
    "app.tasks.feed_tasks.dispatch_unclassified_items": {"queue": QUEUE_PROCESSING},
    "app.tasks.feed_tasks.classify_item": {"queue": QUEUE_PROCESSING},
    "app.tasks.feed_tasks.dispatch_items_missing_articles": {"queue": QUEUE_PROCESSING},
    "app.tasks.feed_tasks.fetch_article": {"queue": QUEUE_PROCESSING},
    "app.tasks.feed_tasks.dispatch_items_missing_iocs": {"queue": QUEUE_PROCESSING},
    "app.tasks.feed_tasks.extract_item_iocs": {"queue": QUEUE_PROCESSING},
    "app.tasks.feed_tasks.reapply_recent_item_tags": {"queue": QUEUE_PROCESSING},
    "app.tasks.feed_tasks.dispatch_new_item_notification_webhooks": {"queue": QUEUE_NOTIFICATIONS},
    "app.tasks.feed_tasks.dispatch_alert_match_notification_webhooks": {"queue": QUEUE_NOTIFICATIONS},
    "app.tasks.feed_tasks.dispatch_feed_failing_notification_webhooks": {"queue": QUEUE_NOTIFICATIONS},
    "app.tasks.feed_tasks.dispatch_webhook_failed_notification_webhooks": {"queue": QUEUE_NOTIFICATIONS},
    "app.tasks.feed_tasks.dispatch_daily_digest_notification_webhooks": {"queue": QUEUE_NOTIFICATIONS},
    "app.tasks.feed_tasks.dispatch_smtp_new_item_notification": {"queue": QUEUE_NOTIFICATIONS},
    "app.tasks.feed_tasks.dispatch_smtp_alert_match_notification": {"queue": QUEUE_NOTIFICATIONS},
    "app.tasks.feed_tasks.dispatch_smtp_feed_failing_notification": {"queue": QUEUE_NOTIFICATIONS},
    "app.tasks.feed_tasks.dispatch_smtp_webhook_failed_notification": {"queue": QUEUE_NOTIFICATIONS},
    "app.tasks.feed_tasks.dispatch_pending_notification_webhook_deliveries": {"queue": QUEUE_NOTIFICATIONS},
    "app.tasks.feed_tasks.process_notification_webhook_deliveries": {"queue": QUEUE_NOTIFICATIONS},
    "app.tasks.feed_tasks.route_integration_event": {"queue": QUEUE_NOTIFICATIONS},
    "app.tasks.feed_tasks.dispatch_pending_integration_events": {"queue": QUEUE_MAINTENANCE},
    "app.tasks.feed_tasks.process_integration_deliveries": {"queue": QUEUE_NOTIFICATIONS},
    "app.tasks.feed_tasks.dispatch_pending_integration_deliveries": {"queue": QUEUE_MAINTENANCE},
    "app.tasks.feed_tasks.maintain_integration_delivery_history": {"queue": QUEUE_MAINTENANCE},
    "app.tasks.feed_tasks.dispatch_items_missing_ai_enrichment": {"queue": QUEUE_AI},
    "app.tasks.feed_tasks.generate_item_ai_enrichment": {"queue": QUEUE_AI},
    "app.tasks.feed_tasks.dispatch_daily_ai_brief_generation": {"queue": QUEUE_AI},
    "app.tasks.feed_tasks.backfill_daily_ai_briefs": {"queue": QUEUE_AI},
    "app.tasks.feed_tasks.reprocess_recent_ai_items": {"queue": QUEUE_AI},
    "app.tasks.feed_tasks.generate_intelligence_report": {"queue": QUEUE_AI},
    "app.tasks.feed_tasks.dispatch_due_report_schedules": {"queue": QUEUE_MAINTENANCE},
    "app.tasks.feed_tasks.reconcile_ai_task_runs": {"queue": QUEUE_MAINTENANCE},
    "app.tasks.feed_tasks.record_beat_heartbeat": {"queue": QUEUE_MAINTENANCE},
    "app.tasks.history_maintenance_tasks.maintain_application_history": {"queue": QUEUE_MAINTENANCE},
}

celery_app = Celery(
    "threatlens",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks.feed_tasks", "app.tasks.history_maintenance_tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_default_queue=QUEUE_DEFAULT,
    task_queues=(
        Queue(QUEUE_DEFAULT),
        Queue(QUEUE_INGEST),
        Queue(QUEUE_PROCESSING),
        Queue(QUEUE_NOTIFICATIONS),
        Queue(QUEUE_AI),
        Queue(QUEUE_MAINTENANCE),
    ),
    task_routes=TASK_ROUTES,
    worker_prefetch_multiplier=1,
    beat_schedule={
        "dispatch-due-feeds": {
            "task": "app.tasks.feed_tasks.dispatch_due_feeds",
            "schedule": 60.0,
        },
        "dispatch-unclassified-items": {
            "task": "app.tasks.feed_tasks.dispatch_unclassified_items",
            "schedule": 300.0,
        },
        "dispatch-items-missing-articles": {
            "task": "app.tasks.feed_tasks.dispatch_items_missing_articles",
            "schedule": 300.0,
        },
        "dispatch-items-missing-iocs": {
            "task": "app.tasks.feed_tasks.dispatch_items_missing_iocs",
            "schedule": 300.0,
        },
        "dispatch-items-missing-ai-enrichment": {
            "task": "app.tasks.feed_tasks.dispatch_items_missing_ai_enrichment",
            "schedule": 300.0,
        },
        "dispatch-feed-metadata-backfill": {
            "task": "app.tasks.feed_tasks.dispatch_feed_metadata_backfill",
            "schedule": 600.0,
        },
        "dispatch-daily-digest-notifications": {
            "task": "app.tasks.feed_tasks.dispatch_daily_digest_notification_webhooks",
            "schedule": 300.0,
        },
        "dispatch-pending-notification-webhooks": {
            "task": "app.tasks.feed_tasks.dispatch_pending_notification_webhook_deliveries",
            "schedule": 60.0,
        },
        "dispatch-pending-integration-events": {
            "task": "app.tasks.feed_tasks.dispatch_pending_integration_events",
            "schedule": 10.0,
        },
        "dispatch-pending-integration-deliveries": {
            "task": "app.tasks.feed_tasks.dispatch_pending_integration_deliveries",
            "schedule": 10.0,
        },
        "maintain-integration-delivery-history": {
            "task": "app.tasks.feed_tasks.maintain_integration_delivery_history",
            "schedule": 3600.0,
        },
        "maintain-application-history": {
            "task": "app.tasks.history_maintenance_tasks.maintain_application_history",
            "schedule": 3600.0,
        },
        "dispatch-daily-ai-brief-generation": {
            "task": "app.tasks.feed_tasks.dispatch_daily_ai_brief_generation",
            "schedule": crontab(minute="*"),
        },
        "dispatch-due-report-schedules": {
            "task": "app.tasks.feed_tasks.dispatch_due_report_schedules",
            "schedule": 60.0,
        },
        "reconcile-ai-task-runs": {
            "task": "app.tasks.feed_tasks.reconcile_ai_task_runs",
            "schedule": 300.0,
        },
        "record-beat-heartbeat": {
            "task": "app.tasks.feed_tasks.record_beat_heartbeat",
            "schedule": float(settings.beat_heartbeat_interval_seconds),
        },
    },
)
