from types import SimpleNamespace

from celery.schedules import crontab

from app.core.logging_config import get_log_context
from app.tasks.celery_app import (
    QUEUE_AI,
    QUEUE_AI_REPORTS,
    QUEUE_AI_REPORTS_EDITORIAL,
    QUEUE_DEFAULT,
    QUEUE_EXPORTS,
    QUEUE_INGEST,
    QUEUE_LIFECYCLE,
    QUEUE_MAINTENANCE,
    QUEUE_NOTIFICATIONS,
    QUEUE_PROCESSING,
    TASK_ROUTES,
    add_task_log_context,
    celery_app,
    complete_task_log_context,
    logger as worker_logger,
    settings,
)


def test_celery_routes_keep_feed_ingestion_off_the_ai_queue():
    assert TASK_ROUTES["app.tasks.feed_tasks.fetch_feed"]["queue"] == QUEUE_INGEST
    assert TASK_ROUTES["app.tasks.feed_tasks.backfill_feed_metadata"]["queue"] == QUEUE_INGEST
    assert TASK_ROUTES["app.tasks.feed_tasks.generate_item_ai_enrichment"]["queue"] == QUEUE_AI
    assert TASK_ROUTES["app.tasks.feed_tasks.dispatch_daily_ai_brief_generation"]["queue"] == QUEUE_AI
    assert TASK_ROUTES["app.tasks.feed_tasks.backfill_daily_ai_briefs"]["queue"] == QUEUE_AI
    assert TASK_ROUTES["app.tasks.feed_tasks.generate_intelligence_report"]["queue"] == QUEUE_AI_REPORTS_EDITORIAL


def test_celery_routes_smtp_notifications_to_notification_queue():
    assert TASK_ROUTES["app.tasks.feed_tasks.dispatch_smtp_new_item_notification"]["queue"] == QUEUE_NOTIFICATIONS
    assert TASK_ROUTES["app.tasks.feed_tasks.dispatch_smtp_alert_match_notification"]["queue"] == QUEUE_NOTIFICATIONS
    assert TASK_ROUTES["app.tasks.feed_tasks.dispatch_smtp_feed_failing_notification"]["queue"] == QUEUE_NOTIFICATIONS
    assert TASK_ROUTES["app.tasks.feed_tasks.dispatch_smtp_webhook_failed_notification"]["queue"] == QUEUE_NOTIFICATIONS


def test_long_exports_route_outside_ingestion_and_processing_pools():
    router = celery_app.amqp.router
    export_queue = router.route({}, "app.tasks.export_tasks.generate_export_job")["queue"].name
    assert export_queue == QUEUE_EXPORTS
    for task in ("fetch_feed", "fetch_article", "classify_item", "extract_item_iocs"):
        queue = router.route({}, f"app.tasks.feed_tasks.{task}")["queue"].name
        assert queue in {QUEUE_INGEST, QUEUE_PROCESSING}
        assert queue != export_queue


def test_celery_declares_expected_named_queues():
    queue_names = {queue.name for queue in celery_app.conf.task_queues}

    assert queue_names == {
        QUEUE_DEFAULT,
        QUEUE_EXPORTS,
        QUEUE_INGEST,
        QUEUE_PROCESSING,
        QUEUE_NOTIFICATIONS,
        QUEUE_AI,
        QUEUE_AI_REPORTS,
        QUEUE_AI_REPORTS_EDITORIAL,
        QUEUE_MAINTENANCE,
        QUEUE_LIFECYCLE,
    }
    assert celery_app.conf.task_default_queue == QUEUE_DEFAULT
    assert celery_app.conf.worker_prefetch_multiplier == 1
    assert celery_app.conf.broker_transport_options["visibility_timeout"] == 3600
    assert celery_app.conf.result_backend_transport_options["visibility_timeout"] == 3600


def test_daily_brief_generation_checks_due_time_on_utc_minute_boundaries():
    generation_schedule = celery_app.conf.beat_schedule["dispatch-daily-ai-brief-generation"]["schedule"]
    reconciliation_schedule = celery_app.conf.beat_schedule["dispatch-daily-digest-notifications"]["schedule"]

    assert isinstance(generation_schedule, crontab)
    assert generation_schedule.minute == set(range(60))
    assert reconciliation_schedule == 300.0


def test_system_health_sampling_and_queue_canaries_are_routed_and_scheduled():
    sampler_task = "app.tasks.system_health_tasks.collect_system_health_sample"
    canary_task = "app.tasks.system_health_tasks.record_queue_execution_canary"
    assert TASK_ROUTES[sampler_task]["queue"] == QUEUE_MAINTENANCE
    assert celery_app.conf.beat_schedule["collect-system-health-sample"] == {
        "task": sampler_task,
        "schedule": 300.0,
    }

    required_queues = [
        QUEUE_DEFAULT,
        QUEUE_EXPORTS,
        QUEUE_INGEST,
        QUEUE_PROCESSING,
        QUEUE_NOTIFICATIONS,
        QUEUE_MAINTENANCE,
        QUEUE_LIFECYCLE,
    ]
    if settings.ai_enabled:
        required_queues.extend([QUEUE_AI, QUEUE_AI_REPORTS, QUEUE_AI_REPORTS_EDITORIAL])
    for queue_name in required_queues:
        schedule = celery_app.conf.beat_schedule[
            f"record-{queue_name}-execution-canary"
        ]
        assert schedule["task"] == canary_task
        assert schedule["args"] == (queue_name,)
        assert schedule["options"] == {
            "queue": queue_name,
            "expires": 120.0,
        }
        assert schedule["options"]["expires"] <= (
            settings.beat_heartbeat_interval_seconds * 2
        )
        assert schedule["options"]["expires"] <= (
            settings.beat_heartbeat_stale_after_seconds
        )


def test_verbose_task_lifecycle_adds_context_without_logging_argument_values(monkeypatch):
    monkeypatch.setattr(settings, "log_detail", "verbose")
    debug_events: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(
        worker_logger,
        "debug",
        lambda message, *args, **kwargs: debug_events.append((message, args, kwargs)),
    )
    task = SimpleNamespace(
        name="app.tasks.example",
        request=SimpleNamespace(delivery_info={"routing_key": "processing"}),
    )

    add_task_log_context(
        task_id="task-123",
        task=task,
        args=("sensitive-argument",),
        kwargs={"api_key": "sensitive-key"},
    )
    assert get_log_context() == {"task_id": "task-123", "task_name": "app.tasks.example"}
    complete_task_log_context(task_id="task-123", task=task, state="SUCCESS")

    assert get_log_context() == {}
    assert [event[0] for event in debug_events] == [
        "task_started positional_arg_count=%s keyword_keys=%s",
        "task_complete state=%s",
    ]
    assert debug_events[0][1] == (1, ["api_key"])
    assert debug_events[1][1] == ("SUCCESS",)
    assert "sensitive-argument" not in repr(debug_events)
    assert "sensitive-key" not in repr(debug_events)


def test_registered_task_producers_ignore_unused_results(monkeypatch):
    from app.tasks import feed_tasks
    sent = []
    def send(name, *_args, **kwargs):
        sent.append((name, kwargs))
        return SimpleNamespace(id="fixture-task")
    monkeypatch.setattr(celery_app, "send_task", send)
    feed_tasks.fetch_feed.delay("fixture-feed")
    feed_tasks.fetch_feed.apply_async(args=["fixture-feed"])
    assert len(sent) == 2
    assert all(options["ignore_result"] is True for _name, options in sent)


def test_metadata_named_task_producer_explicitly_ignores_results(monkeypatch):
    from app.api.routes.feeds import _enqueue_metadata_backfills
    sent = []
    monkeypatch.setattr(celery_app, "send_task", lambda name, **kwargs: sent.append((name, kwargs)))
    assert _enqueue_metadata_backfills(["fixture-feed"], 1) == 1
    assert sent == [("app.tasks.feed_tasks.backfill_feed_metadata", {"args": ["fixture-feed"], "ignore_result": True})]
