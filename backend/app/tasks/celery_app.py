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
    },
)
