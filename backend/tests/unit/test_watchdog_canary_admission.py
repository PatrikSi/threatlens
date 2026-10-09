"""Exercise the scheduler selected by the shipped watchdog command."""

import shelve

import pytest
from celery import Celery
from kombu.utils.imports import symbol_by_name

from app.core.config import get_settings
from app.tasks import beat_scheduler, bounded_beat
from app.tasks.beat_watchdog import build_beat_command


class _HeartbeatClient:
    def __init__(self):
        self.writes = []

    def set(self, key, value, *, ex):
        self.writes.append((key, value, ex))


@pytest.mark.parametrize(("task_name", "capacity", "publication_count"), [
    (bounded_beat.CANARY_TASK, False, 0),
    (bounded_beat.CANARY_TASK, True, 1),
    ("synthetic_regular_task", False, 1),
], ids=["full-canary-queue", "available-canary-queue", "regular-task"])
def test_command_selected_scheduler_bounds_canaries_and_keeps_durable_heartbeat(
    monkeypatch, tmp_path, task_name, capacity, publication_count,
):
    settings = get_settings()
    command = build_beat_command(settings)
    scheduler_path = next(argument.split("=", 1)[1] for argument in command
                          if argument.startswith("--scheduler="))
    scheduler_class = symbol_by_name(scheduler_path)
    heartbeat = _HeartbeatClient()
    monkeypatch.setattr(beat_scheduler, "redis_client_from_url", lambda *_args, **_kwargs: heartbeat)
    inspected = []

    def has_capacity(app, queue):
        inspected.append((app, queue))
        return capacity

    monkeypatch.setattr(bounded_beat, "canary_queue_has_capacity", has_capacity)
    app = Celery("watchdog-admission-test", broker="memory://", set_as_current=False)
    app.conf.update(result_expires=None, beat_schedule={
        "probe": {"task": task_name, "schedule": 30, "options": {"queue": "control-v1"}},
    })
    schedule_path = str(tmp_path / "selected-scheduler")
    scheduler = scheduler_class(app=app, schedule_filename=schedule_path)
    published = []

    def publish(*args, **kwargs):
        published.append((args, kwargs))

    monkeypatch.setattr(scheduler, "send_task", publish)
    registered_task = app.tasks.get(task_name)
    if registered_task is not None:
        monkeypatch.setattr(registered_task, "apply_async", publish)
    try:
        scheduler.apply_async(scheduler.schedule["probe"])
        assert len(published) == publication_count
        assert inspected == ([(app, "control-v1")] if task_name == bounded_beat.CANARY_TASK else [])
        assert scheduler.schedule["probe"].total_run_count == 1
        assert scheduler._last_sync is not None
        assert scheduler._tasks_since_sync == 0
        # The reserved next occurrence is not due, but a real scheduler tick
        # must still publish the watchdog heartbeat.
        scheduler.tick()
        assert len(heartbeat.writes) == 1
        assert heartbeat.writes[0][0] == settings.beat_scheduler_heartbeat_key
        assert heartbeat.writes[0][2] == settings.beat_heartbeat_ttl_seconds
        assert len(published) == publication_count
    finally:
        scheduler.close()
        app.close()
    with shelve.open(schedule_path) as persisted:
        assert persisted["entries"]["probe"].total_run_count == 1
