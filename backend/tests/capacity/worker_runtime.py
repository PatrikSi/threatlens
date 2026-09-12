"""Real prefork worker entry point for the isolated recovery experiment."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from celery.signals import before_task_publish, task_postrun, task_prerun, worker_ready

from app.tasks.celery_app import celery_app

EVENT_PATH = Path(sys.argv[1])


def record(kind, **values):
    row = (
        json.dumps(
            {
                "kind": kind,
                "pid": os.getpid(),
                "monotonic_ns": time.monotonic_ns(),
                **values,
            }
        )
        + "\n"
    )
    descriptor = os.open(EVENT_PATH, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, row.encode())
    finally:
        os.close(descriptor)


@worker_ready.connect(weak=False)
def ready(**_kwargs):
    record("ready")


@before_task_publish.connect(weak=False)
def published(headers=None, **_kwargs):
    headers["capacity_published_ns"] = time.monotonic_ns()


@task_prerun.connect(weak=False)
def started(task_id=None, task=None, args=None, **_kwargs):
    headers = getattr(task.request, "headers", None) or {}
    record(
        "task_started",
        task_id=task_id,
        task_name=task.name,
        args=args,
        published_ns=headers.get("capacity_published_ns"),
    )


@task_postrun.connect(weak=False)
def finished(task_id=None, task=None, retval=None, state=None, **_kwargs):
    record(
        "task_finished",
        task_id=task_id,
        task_name=task.name,
        state=state,
        result_status=retval.get("status") if isinstance(retval, dict) else None,
    )


celery_app.worker_main(
    [
        "worker",
        "--pool=prefork",
        "--concurrency=1",
        "--without-mingle",
        "--without-gossip",
        "--loglevel=WARNING",
        "--queues=default,ingest,processing,notifications,ai,ai-reports-v2,ai-reports-v3,maintenance",
    ]
)
