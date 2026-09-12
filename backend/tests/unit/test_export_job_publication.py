import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import uuid
from contextlib import contextmanager

from app.tasks import export_tasks


def test_export_publisher_isolated_from_worker_transport_and_result_channels(monkeypatch):
    captured = {}
    original_options = dict(export_tasks.celery_app.conf.broker_transport_options)
    connection = object()

    @contextmanager
    def write_connection(**kwargs):
        captured["connection_options"] = kwargs
        yield connection
        captured["closed"] = True

    monkeypatch.setattr(export_tasks.celery_app, "connection_for_write", write_connection)
    monkeypatch.setattr(export_tasks.generate_export_job, "apply_async", lambda **kwargs: captured.update(kwargs))
    job_id = uuid.uuid4()
    assert export_tasks._publish_export_job(job_id)
    assert captured["args"] == [str(job_id)]
    assert captured["connection"] is connection
    assert captured["closed"] and captured["retry"] is False and captured["ignore_result"] is True
    options = captured["connection_options"]["transport_options"]
    assert options["socket_connect_timeout"] == options["socket_timeout"] == 2
    assert options["max_retries"] == 0
    assert options["visibility_timeout"] == original_options["visibility_timeout"]
    assert export_tasks.celery_app.conf.broker_transport_options == original_options


def test_nonresponsive_local_broker_does_not_strand_export_publication(tmp_path):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(.1)
    port = listener.getsockname()[1]
    stop = threading.Event()
    accepted = threading.Event()
    peers = []

    def receive_without_replying():
        while not stop.is_set():
            try:
                peer, _address = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            peers.append(peer)
            accepted.set()

    thread = threading.Thread(target=receive_without_replying, daemon=True)
    thread.start()
    # A fresh process bounds regressions even if a library read hangs. It uses
    # only this localhost peer, synthetic settings, and an empty working dir.
    env = {
        "PATH": os.environ["PATH"],
        "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
        "APP_ENV": "development",
        "DATABASE_URL": "sqlite://",
        "REDIS_URL": f"redis://127.0.0.1:{port}/0",
        "REDIS_CONNECT_TIMEOUT_SECONDS": "0.1",
        "REDIS_SOCKET_TIMEOUT_SECONDS": "0.1",
        "JWT_SECRET": "export-publication-test-secret-only",
        "APP_DATA_ENCRYPTION_KEY": "export-publication-test-encryption-only",
    }
    code = """
import time
import uuid
from app.tasks.export_tasks import _publish_export_job
started = time.monotonic()
assert _publish_export_job(uuid.uuid4()) is False
assert time.monotonic() - started < 2
print('publication returned for durable retry')
"""
    try:
        result = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=env,
                                capture_output=True, text=True, timeout=15, check=False)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "publication returned for durable retry" in result.stdout
        assert accepted.is_set() and len(peers) == 1
    finally:
        stop.set()
        listener.close()
        thread.join(timeout=1)
        for peer in peers:
            peer.close()


def test_export_acceptance_survives_publication_reservation_failure(monkeypatch):
    monkeypatch.setattr(export_tasks, "read_queue_execution_canaries", lambda **_kwargs: {})

    def unavailable_session():
        raise ConnectionError("Database temporarily unavailable")

    def unexpected_publish(_job_id):
        raise AssertionError("Publication must follow a committed reservation")

    monkeypatch.setattr(export_tasks.session_module, "SessionLocal", unavailable_session)
    monkeypatch.setattr(export_tasks, "_publish_export_job", unexpected_publish)
    assert export_tasks.enqueue_export_job(uuid.uuid4()) is False
