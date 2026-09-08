"""Disposable solo consumers for the real-broker worker-isolation regression."""

import os
from pathlib import Path
import sys
import time

from celery import Celery
from celery.signals import worker_ready


app = Celery("export-isolation-consumer", broker=os.environ["THREATLENS_FIXTURE_BROKER"])
app.conf.update(task_ignore_result=True, worker_prefetch_multiplier=1)
queue = sys.argv[1]


@worker_ready.connect(weak=False)
def ready(**_kwargs):
    Path(f"ready-{queue}").touch()


@app.task(name="review.queue_isolation.export")
def slow_export():
    Path("export-started").touch()
    deadline = time.monotonic() + 30
    while not Path("release-export").exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("Fixture did not release its export consumer")
        time.sleep(0.025)
    Path("export-completed").touch()


@app.task(name="review.queue_isolation.processing")
def process_item():
    Path("processing-completed").touch()


if __name__ == "__main__":
    app.worker_main([
        "worker", "--pool=solo", "--concurrency=1", f"--queues={queue}",
        f"--hostname={queue}@fixture", "--without-gossip", "--without-mingle",
        "--without-heartbeat", "--loglevel=WARNING",
    ])
