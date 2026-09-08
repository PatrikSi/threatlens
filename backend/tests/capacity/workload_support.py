from __future__ import annotations

import json
import math
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from sqlalchemy import text

PROFILES = {
    "smoke": {
        "seed_items": 30,
        "article_bytes": 8192,
        "feeds": 2,
        "items_per_feed": 2,
        "operations": 4,
        "provider_delay_seconds": 0.06,
        "worker_concurrency": 2,
    },
    "baseline": {
        "seed_items": 500,
        "article_bytes": 32768,
        "feeds": 4,
        "items_per_feed": 10,
        "operations": 20,
        "provider_delay_seconds": 0.10,
        "worker_concurrency": 4,
    },
    "sustained": {
        "seed_items": 200,
        "article_bytes": 8192,
        "feeds": 2,
        "items_per_feed": 2,
        "operations": 0,
        "provider_delay_seconds": 0.1,
        "worker_concurrency": 2,
        "service_interval_seconds": 2,
        "feed_interval_seconds": 10,
        "arrival_model": "paced_closed_loop",
        "governance_phase_seconds": 0.5,
        "ai_phase_seconds": 0.25,
    },
    "large": {
        "seed_items": 2000,
        "article_bytes": 65536,
        "feeds": 8,
        "items_per_feed": 20,
        "operations": 40,
        "provider_delay_seconds": 0.15,
        "worker_concurrency": 4,
    },
}
QUEUES = (
    "default",
    "ingest",
    "processing",
    "notifications",
    "ai",
    "ai-reports-v2",
    "maintenance",
)


def percentile(values, fraction):
    ordered = sorted(values)
    if not ordered:
        return 0.0
    return round(
        ordered[min(len(ordered) - 1, max(0, math.ceil(len(ordered) * fraction) - 1))],
        3,
    )


def summarize(values):
    return {
        "count": len(values),
        "p50_ms": percentile(values, 0.5),
        "p95_ms": percentile(values, 0.95),
        "max_ms": round(max(values, default=0), 3),
    }


def rss_bytes():
    with open("/proc/self/statm", encoding="ascii") as source:
        return int(source.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")


class Measurements:
    def __init__(self):
        self.latencies = {}
        self.outcomes = {}
        self.lock = threading.Lock()
        self.rss_start = rss_bytes()
        self.rss_peak = self.rss_start
        self.queue_peak = 0
        self.db_lock_wait_peak_ms = 0.0
        self.db_lock_samples = 0
        self.db_waiting_peak = 0
        self.samples = 0
        self.sampler_errors = []
        self.pending_messages = {}
        self.started_messages = set()
        self.oldest_pending_age_peak_ms = 0

    @contextmanager
    def operation(self, name):
        start = time.perf_counter()
        operation = {}
        try:
            yield operation
        finally:
            elapsed = (time.perf_counter() - start) * 1000
            with self.lock:
                self.latencies.setdefault(name, []).append(elapsed)
                if operation.get("outcome"):
                    self.latencies.setdefault(
                        name + ":" + operation["outcome"], []
                    ).append(elapsed)

    def outcome(self, name):
        with self.lock:
            self.outcomes[name] = self.outcomes.get(name, 0) + 1

    def published(self, headers):
        with self.lock:
            if headers["id"] not in self.started_messages:
                self.pending_messages[headers["id"]] = headers["capacity_published_ns"]

    def task_started(self, task_id, task):
        published = (getattr(task.request, "headers", None) or {}).get(
            "capacity_published_ns"
        )
        with self.lock:
            self.started_messages.add(task_id)
            self.pending_messages.pop(task_id, None)
            if published is not None:
                self.latencies.setdefault(
                    "queue_wait:" + task.name.rsplit(".", 1)[-1], []
                ).append((time.monotonic_ns() - published) / 1e6)

    def sample(self, engine, redis_client, stop):
        try:
            with engine.connect() as connection:
                while not stop.is_set():
                    self.rss_peak = max(self.rss_peak, rss_bytes())
                    with self.lock:
                        if self.pending_messages:
                            self.oldest_pending_age_peak_ms = max(
                                self.oldest_pending_age_peak_ms,
                                (
                                    time.monotonic_ns()
                                    - min(self.pending_messages.values())
                                )
                                / 1e6,
                            )
                    self.queue_peak = max(
                        self.queue_peak,
                        sum(redis_client.llen(queue) for queue in QUEUES)
                        + redis_client.hlen("unacked"),
                    )
                    waits = (
                        connection.execute(
                            text("""
                        SELECT coalesce(extract(epoch FROM clock_timestamp() - query_start) * 1000, 0)
                        FROM pg_stat_activity
                        WHERE datname = current_database() AND wait_event_type = 'Lock'
                          AND application_name LIKE 'threatlens-capacity%'
                    """)
                        )
                        .scalars()
                        .all()
                    )
                    connection.commit()
                    self.db_lock_wait_peak_ms = max(
                        self.db_lock_wait_peak_ms, *[float(v) for v in waits], 0
                    )
                    self.db_lock_samples += len(waits)
                    self.db_waiting_peak = max(self.db_waiting_peak, len(waits))
                    self.samples += 1
                    if self.samples % 25 == 0 and os.environ.get(
                        "THREATLENS_CAPACITY_OUTPUT"
                    ):
                        with self.lock:
                            partial = self.report()
                        partial.update(
                            run_id=os.environ.get("THREATLENS_CAPACITY_RUN_ID"),
                            status="in_progress",
                        )
                        path = Path(
                            os.environ["THREATLENS_CAPACITY_OUTPUT"] + ".partial.json"
                        )
                        path.parent.mkdir(parents=True, exist_ok=True)
                        temporary = path.with_suffix(".tmp")
                        temporary.write_text(json.dumps(partial, sort_keys=True))
                        temporary.replace(path)
                    stop.wait(0.02)
        except Exception as exc:
            self.sampler_errors.append(type(exc).__name__)

    def report(self):
        return {
            "latencies": {
                name: summarize(values)
                for name, values in sorted(self.latencies.items())
            },
            "outcomes": self.outcomes,
            "memory": {
                "process_rss_start_bytes": self.rss_start,
                "process_rss_peak_bytes": self.rss_peak,
                "process_rss_increase_bytes": max(0, self.rss_peak - self.rss_start),
            },
            "database": {
                "sampled_lock_waiting_query_age_peak_ms": round(
                    self.db_lock_wait_peak_ms, 3
                ),
                "lock_wait_samples": self.db_lock_samples,
                "waiting_sessions_peak": self.db_waiting_peak,
            },
            "queue": {
                "depth_peak": self.queue_peak,
                "oldest_pending_age_peak_ms": round(self.oldest_pending_age_peak_ms, 3),
            },
            "sampler": {
                "interval_ms": 20,
                "samples": self.samples,
                "errors": self.sampler_errors,
            },
        }


@contextmanager
def local_sources(profile, state=None):
    state = state if state is not None else {}
    source_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            if self.path.startswith("/deadline/headers"):
                time.sleep(0.4)
            if self.path.startswith("/feed/"):
                index = int(self.path.rsplit("/", 1)[1])
                count = profile["items_per_feed"] * (3 if index == 0 else 1)
                with source_lock:
                    generation = (
                        state.get("generation", 0)
                        if profile.get("duration_seconds")
                        else 0
                    )
                    state["generation"] = generation + 1
                    state["issued_items"] = state.get("issued_items", 0) + count
                entries = "".join(
                    f"<item><guid>capacity-{generation}-{index}-{entry}</guid><title>Vulnerability research {index}-{entry}</title>"
                    f"<link>{base}/article/{generation}-{index}-{entry}</link><description>Security advisory evidence</description></item>"
                    for entry in range(count)
                )
                body = f'<rss version="2.0"><channel><title>Capacity feed {index}</title><link>{base}</link><description>Capacity</description>{entries}</channel></rss>'.encode()
                content_type = "application/rss+xml"
            else:
                body = (
                    "<html><head><title>Security research</title></head><body><article><h1>Vulnerability research</h1><p>"
                    + "Threat intelligence identifies a vulnerability requiring a patch. "
                    * 64
                    + "</p></article></body></html>"
                ).encode()
                content_type = "text/html"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            time.sleep(profile["provider_delay_seconds"])
            body = json.dumps(
                {
                    "model": "capacity-local",
                    "choices": [
                        {
                            "message": {"content": '{"ok":true,"message":"ready"}'},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 10,
                        "total_tokens": 20,
                    },
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    base = f"http://127.0.0.1:{server.server_port}"
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield base
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
