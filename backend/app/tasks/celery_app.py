from celery import Celery

from app.core.config import get_settings

settings = get_settings()

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
    beat_schedule={
        "dispatch-due-feeds": {
            "task": "app.tasks.feed_tasks.dispatch_due_feeds",
            "schedule": 60.0,
        },
        "dispatch-unclassified-items": {
            "task": "app.tasks.feed_tasks.dispatch_unclassified_items",
            "schedule": 300.0,
        },
        "dispatch-items-missing-iocs": {
            "task": "app.tasks.feed_tasks.dispatch_items_missing_iocs",
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
        "dispatch-daily-ai-brief-generation": {
            "task": "app.tasks.feed_tasks.dispatch_daily_ai_brief_generation",
            "schedule": 3600.0,
        },
        "record-beat-heartbeat": {
            "task": "app.tasks.feed_tasks.record_beat_heartbeat",
            "schedule": float(settings.beat_heartbeat_interval_seconds),
        },
    },
)
