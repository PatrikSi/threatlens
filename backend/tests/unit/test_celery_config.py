from celery.schedules import crontab

from app.tasks.celery_app import (
    QUEUE_AI,
    QUEUE_DEFAULT,
    QUEUE_INGEST,
    QUEUE_MAINTENANCE,
    QUEUE_NOTIFICATIONS,
    QUEUE_PROCESSING,
    TASK_ROUTES,
    celery_app,
)


def test_celery_routes_keep_feed_ingestion_off_the_ai_queue():
    assert TASK_ROUTES["app.tasks.feed_tasks.fetch_feed"]["queue"] == QUEUE_INGEST
    assert TASK_ROUTES["app.tasks.feed_tasks.backfill_feed_metadata"]["queue"] == QUEUE_INGEST
    assert TASK_ROUTES["app.tasks.feed_tasks.generate_item_ai_enrichment"]["queue"] == QUEUE_AI
    assert TASK_ROUTES["app.tasks.feed_tasks.dispatch_daily_ai_brief_generation"]["queue"] == QUEUE_AI
    assert TASK_ROUTES["app.tasks.feed_tasks.backfill_daily_ai_briefs"]["queue"] == QUEUE_AI


def test_celery_routes_smtp_notifications_to_notification_queue():
    assert TASK_ROUTES["app.tasks.feed_tasks.dispatch_smtp_new_item_notification"]["queue"] == QUEUE_NOTIFICATIONS
    assert TASK_ROUTES["app.tasks.feed_tasks.dispatch_smtp_alert_match_notification"]["queue"] == QUEUE_NOTIFICATIONS
    assert TASK_ROUTES["app.tasks.feed_tasks.dispatch_smtp_feed_failing_notification"]["queue"] == QUEUE_NOTIFICATIONS
    assert TASK_ROUTES["app.tasks.feed_tasks.dispatch_smtp_webhook_failed_notification"]["queue"] == QUEUE_NOTIFICATIONS


def test_celery_declares_expected_named_queues():
    queue_names = {queue.name for queue in celery_app.conf.task_queues}

    assert queue_names == {
        QUEUE_DEFAULT,
        QUEUE_INGEST,
        QUEUE_PROCESSING,
        QUEUE_NOTIFICATIONS,
        QUEUE_AI,
        QUEUE_MAINTENANCE,
    }
    assert celery_app.conf.task_default_queue == QUEUE_DEFAULT
    assert celery_app.conf.worker_prefetch_multiplier == 1


def test_daily_brief_generation_checks_due_time_on_utc_minute_boundaries():
    generation_schedule = celery_app.conf.beat_schedule["dispatch-daily-ai-brief-generation"]["schedule"]
    reconciliation_schedule = celery_app.conf.beat_schedule["dispatch-daily-digest-notifications"]["schedule"]

    assert isinstance(generation_schedule, crontab)
    assert generation_schedule.minute == set(range(60))
    assert reconciliation_schedule == 300.0
