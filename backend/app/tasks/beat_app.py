"""Publish the worker's schedule without importing worker implementations."""

from copy import deepcopy

from celery import Celery

from app.tasks.celery_app import celery_app as worker_app


class BeatCelery(Celery):
    def send_task(self, *args, **options):
        # Beat sends unregistered task names. Unlike Task.apply_async, Celery's
        # send_task defaults ignore_result to False rather than the app setting.
        options.setdefault("ignore_result", self.conf.task_ignore_result)
        return super().send_task(*args, **options)


beat_app = BeatCelery("threatlens", set_as_current=False)
beat_app.conf.update(
    {
        **deepcopy(dict(worker_app.conf)),
        "include": (),
        "imports": (),
    }
)
