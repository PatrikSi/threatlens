"""Exercise configured task routing with independent real broker consumers."""

import os
from pathlib import Path
import subprocess
import sys
import time

from celery import Celery

from app.core.worker_queues import QUEUE_EXPORTS, QUEUE_PROCESSING
from app.tasks.celery_app import TASK_ROUTES


def _wait_for(path: Path, workers: list[subprocess.Popen], timeout: float = 15):
    deadline = time.monotonic() + timeout
    while not path.exists():
        assert all(worker.poll() is None for worker in workers), "Fixture worker exited early"
        assert time.monotonic() < deadline, f"Timed out waiting for {path.name}"
        time.sleep(0.025)


def test_busy_export_consumer_does_not_block_processing(test_redis_url, tmp_path):
    workers, logs = [], []
    app = Celery("export-isolation-producer", broker=test_redis_url, set_as_current=False)
    app.conf.update(task_routes={
        "review.queue_isolation.export": TASK_ROUTES["app.tasks.export_tasks.generate_export_job"],
        "review.queue_isolation.processing": TASK_ROUTES["app.tasks.feed_tasks.classify_item"],
    }, task_ignore_result=True)
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
        "APP_ENV": "development",
        "THREATLENS_FIXTURE_BROKER": test_redis_url,
    }
    try:
        for queue in (QUEUE_EXPORTS, QUEUE_PROCESSING):
            log = (tmp_path / f"{queue}.log").open("w")
            logs.append(log)
            workers.append(subprocess.Popen(
                [sys.executable, "-m", "tests.integration.export_worker_fixture", queue],
                cwd=tmp_path, env=environment, stdout=log, stderr=subprocess.STDOUT,
            ))
        for queue in (QUEUE_EXPORTS, QUEUE_PROCESSING):
            _wait_for(tmp_path / f"ready-{queue}", workers)
        app.send_task("review.queue_isolation.export", ignore_result=True, retry=False)
        _wait_for(tmp_path / "export-started", workers)
        app.send_task("review.queue_isolation.processing", ignore_result=True, retry=False)
        _wait_for(tmp_path / "processing-completed", workers, timeout=5)
        assert not (tmp_path / "export-completed").exists()
    finally:
        (tmp_path / "release-export").touch()
        for worker in workers:
            worker.terminate()
            try:
                worker.wait(timeout=5)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait(timeout=5)
        for log in logs:
            log.close()
        app.close()
