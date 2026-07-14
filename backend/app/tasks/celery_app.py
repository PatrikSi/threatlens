from celery import Celery
from kombu import Queue

from app.core.config import get_settings

settings = get_settings()

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
    "app.tasks.feed_tasks.dispatch_items_missing_ai_enrichment": {"queue": QUEUE_AI},
    "app.tasks.feed_tasks.generate_item_ai_enrichment": {"queue": QUEUE_AI},
    "app.tasks.feed_tasks.dispatch_daily_ai_brief_generation": {"queue": QUEUE_AI},
    "app.tasks.feed_tasks.backfill_daily_ai_briefs": {"queue": QUEUE_AI},
    "app.tasks.feed_tasks.reprocess_recent_ai_items": {"queue": QUEUE_AI},
    "app.tasks.feed_tasks.reconcile_ai_task_runs": {"queue": QUEUE_MAINTENANCE},
    "app.tasks.feed_tasks.record_beat_heartbeat": {"queue": QUEUE_MAINTENANCE},
}

celery_app = Celery(
    "threatlens",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks.feed_tasks"],
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
            "schedule": 3600.0,
        },
        "dispatch-pending-notification-webhooks": {
            "task": "app.tasks.feed_tasks.dispatch_pending_notification_webhook_deliveries",
            "schedule": 60.0,
        },
        "dispatch-pending-integration-events": {
            "task": "app.tasks.feed_tasks.dispatch_pending_integration_events",
            "schedule": 10.0,
        },
        "dispatch-daily-ai-brief-generation": {
            "task": "app.tasks.feed_tasks.dispatch_daily_ai_brief_generation",
            "schedule": 300.0,
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
