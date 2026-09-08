from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone

from app.services.lifecycle_catalog import next_scheduled_at
from app.tasks.alert_tasks import maintain_alert_history_task
from app.tasks.celery_app import celery_app
from app.tasks.history_maintenance_tasks import maintain_application_history
from app.tasks.integration_tasks import maintain_integration_delivery_history


def test_lifecycle_schedule_rolls_daily_and_weekly_without_drift():
    now = datetime(2026, 4, 7, 14, 30, tzinfo=timezone.utc)
    assert next_scheduled_at(
        cadence="daily", hour_utc=3, weekday=None, after=now
    ) == datetime(2026, 4, 8, 3, tzinfo=timezone.utc)
    weekly = next_scheduled_at(cadence="weekly", hour_utc=2, weekday=0, after=now)
    assert weekly.weekday() == 0
    assert weekly > now
    assert weekly - now < timedelta(days=7)


def test_beat_uses_new_housekeeping_names_during_mixed_version_rollout():
    schedule = celery_app.conf.beat_schedule
    routes = celery_app.conf.task_routes
    assert "dispatch-due-lifecycle-runs" in schedule
    assert schedule["run-lifecycle-housekeeping"]["schedule"] == 900.0
    assert routes["app.tasks.lifecycle_tasks.dispatch_due_lifecycle_runs"] == {
        "queue": "lifecycle-v1"
    }
    assert routes["app.tasks.lifecycle_tasks.execute_lifecycle_run"] == {
        "queue": "lifecycle-v1"
    }
    assert routes["app.tasks.lifecycle_tasks.run_lifecycle_housekeeping"] == {
        "queue": "lifecycle-v1"
    }
    assert "lifecycle-v1" in {queue.name for queue in celery_app.conf.task_queues}
    lifecycle_canary = schedule["record-lifecycle-v1-execution-canary"]
    assert lifecycle_canary["args"] == ("lifecycle-v1",)
    assert lifecycle_canary["options"]["queue"] == "lifecycle-v1"
    assert "maintain-application-history" not in schedule
    assert "maintain-integration-delivery-history" not in schedule
    assert "maintain-alert-history" not in schedule


def test_legacy_task_entrypoints_are_non_deleting_compatibility_shims():
    application_source = inspect.getsource(maintain_application_history.run)
    integration_source = inspect.getsource(maintain_integration_delivery_history.run)
    alert_source = inspect.getsource(maintain_alert_history_task.run)

    assert "run_application_security_housekeeping" in application_source
    assert "prune_application_history" not in application_source
    assert "run_integration_delivery_housekeeping" in integration_source
    assert "run_integration_delivery_maintenance" not in integration_source
    assert "prune_occurrences=False" in alert_source
    assert "prune_evaluations=False" in alert_source
    assert "prune_metrics=False" in alert_source
