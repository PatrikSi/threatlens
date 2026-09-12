import uuid

from celery import Celery

from app.tasks.bounded_beat import (
    BoundedCanaryScheduler,
    CANARY_TASK,
    MAX_CANARY_QUEUE_DEPTH,
    canary_queue_has_capacity,
)


def test_paused_redis_consumer_has_bounded_canaries_and_recovers(
    test_redis_url, tmp_path
):
    queue = f"canary-test-{uuid.uuid4().hex}"
    app = Celery("bounded-beat-test", broker=test_redis_url, set_as_current=False)
    app.conf.update(
        task_default_queue=queue,
        broker_transport_options={
            "global_keyprefix": f"{queue}:",
            "priority_steps": [0, 2, 6],
        },
        beat_schedule={
            "canary": {
                "task": CANARY_TASK,
                "schedule": 30,
                "args": (queue,),
                "options": {"queue": queue, "expires": 60},
            }
        },
    )
    scheduler = BoundedCanaryScheduler(
        app=app, schedule_filename=str(tmp_path / "schedule")
    )
    try:
        # Different priority lists and a prefix must count toward the same budget.
        for priority in (0, 2, 6):
            app.send_task("synthetic_work", queue=queue, priority=priority)
        for _ in range(60):
            scheduler.apply_async(scheduler.schedule["canary"])
        with app.connection_for_write() as connection:
            with connection.channel() as channel:
                assert channel._size(queue) == MAX_CANARY_QUEUE_DEPTH
                messages = [channel._get(queue) for _ in range(MAX_CANARY_QUEUE_DEPTH)]
                assert (
                    sum(
                        message["headers"]["task"] == CANARY_TASK
                        for message in messages
                    )
                    == MAX_CANARY_QUEUE_DEPTH - 3
                )
                assert channel._size(queue) == 0
        assert scheduler.schedule["canary"].total_run_count == 60
        assert canary_queue_has_capacity(app, queue)
        scheduler.apply_async(scheduler.schedule["canary"])
        with app.connection_for_write() as connection:
            with connection.channel() as channel:
                assert channel._size(queue) == 1
                assert channel._get(queue)["headers"]["task"] == CANARY_TASK
    finally:
        scheduler.close()
        app.close()


def test_admission_outage_advances_schedule_without_publication(monkeypatch, tmp_path):
    app = Celery("bounded-beat-outage", broker="memory://", set_as_current=False)
    app.conf.beat_schedule = {
        "canary": {
            "task": CANARY_TASK,
            "schedule": 30,
            "args": ("processing",),
            "options": {"queue": "processing"},
        }
    }
    scheduler = BoundedCanaryScheduler(
        app=app, schedule_filename=str(tmp_path / "schedule")
    )

    def unavailable(**_kwargs):
        raise ConnectionError("Synthetic broker outage")

    monkeypatch.setattr(app, "connection_for_write", unavailable)
    try:
        for _ in range(3):
            assert scheduler.apply_async(scheduler.schedule["canary"]) is None
        assert scheduler.schedule["canary"].total_run_count == 3
    finally:
        scheduler.close()
        app.close()
