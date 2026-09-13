from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from sqlalchemy.engine import make_url


class OwnedWorker:
    def __init__(self, *, directory, database_url, redis_url):
        self.directory = directory
        self.events_path = directory / "worker-events.jsonl"
        self.log = (directory / "worker.log").open("w")
        env = dict(
            os.environ,
            DATABASE_URL=make_url(database_url)
            .update_query_dict(
                {"application_name": "threatlens-capacity-recovery-worker"}
            )
            .render_as_string(hide_password=False),
            REDIS_URL=redis_url,
            AI_ENABLED="true",
            ALLOW_PRIVATE_NETWORK_FETCH="true",
            ALLOW_PRIVATE_NETWORK_AI="true",
            DISPATCH_ITEMS_MISSING_ARTICLES_AFTER_SECONDS="0",
            PYTHONPATH=str(Path(__file__).resolve().parents[2]),
        )
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "tests.capacity.worker_runtime",
                str(self.events_path),
            ],
            cwd=directory,
            env=env,
            stdout=self.log,
            stderr=subprocess.STDOUT,
        )

    def events(self):
        if not self.events_path.exists():
            return []
        lines = self.events_path.read_text().splitlines()
        return [json.loads(line) for line in lines if line.endswith("}")]

    def wait_ready(self):
        until = time.monotonic() + 45
        while time.monotonic() < until:
            if any(event["kind"] == "ready" for event in self.events()):
                return
            if self.process.poll() is not None:
                raise RuntimeError(
                    "owned worker exited before ready: "
                    + (self.directory / "worker.log").read_text()[-2000:]
                )
            time.sleep(0.1)
        raise TimeoutError("owned worker did not start")

    def kill_task_child(self, task_id):
        candidates = [
            event
            for event in self.events()
            if event.get("task_id") == task_id and event["kind"] == "task_started"
        ]
        assert candidates
        pid = candidates[-1]["pid"]
        # Accept only a currently attached child of this exact Popen worker.
        children = {
            int(value)
            for value in Path(
                f"/proc/{self.process.pid}/task/{self.process.pid}/children"
            )
            .read_text()
            .split()
        }
        assert pid in children and pid != self.process.pid
        os.kill(pid, signal.SIGKILL)
        return pid

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                children = Path(
                    f"/proc/{self.process.pid}/task/{self.process.pid}/children"
                )
                for pid in children.read_text().split() if children.exists() else []:
                    try:
                        os.kill(int(pid), signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                self.process.kill()
                self.process.wait(timeout=5)
        self.log.close()
