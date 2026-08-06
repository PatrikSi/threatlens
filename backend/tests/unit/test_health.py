from types import SimpleNamespace

from app.api.routes import health


class _Inspector:
    def __init__(self, *, queues_by_worker):
        self.queues_by_worker = queues_by_worker

    def ping(self):
        return {worker: {"ok": "pong"} for worker in self.queues_by_worker}

    def active_queues(self):
        return {
            worker: [{"name": queue_name} for queue_name in queue_names]
            for worker, queue_names in self.queues_by_worker.items()
        }


def _install_inspector(monkeypatch, queues_by_worker):
    monkeypatch.setattr(
        health.celery_app.control,
        "inspect",
        lambda timeout: _Inspector(queues_by_worker=queues_by_worker),
    )


def test_worker_health_requires_all_non_ai_queues(monkeypatch):
    _install_inspector(
        monkeypatch,
        {
            "worker@test": ["ingest", "processing", "notifications", "maintenance"],
        },
    )
    settings = SimpleNamespace(health_worker_ping_timeout_seconds=1.0, ai_enabled=False)

    ok, workers, queue_snapshot = health._worker_health_snapshot(settings)

    assert ok is True
    assert workers == {"worker@test": "pong"}
    assert queue_snapshot["missing"] == []


def test_worker_health_reports_missing_required_queue(monkeypatch):
    _install_inspector(
        monkeypatch,
        {
            "worker@test": ["ingest", "processing"],
        },
    )
    settings = SimpleNamespace(health_worker_ping_timeout_seconds=1.0, ai_enabled=False)

    ok, _workers, queue_snapshot = health._worker_health_snapshot(settings)

    assert ok is False
    assert queue_snapshot["missing"] == ["maintenance", "notifications"]


def test_worker_health_requires_ai_queue_when_ai_enabled(monkeypatch):
    _install_inspector(
        monkeypatch,
        {
            "worker@test": ["ingest", "processing", "notifications", "maintenance"],
        },
    )
    settings = SimpleNamespace(health_worker_ping_timeout_seconds=1.0, ai_enabled=True)

    ok, _workers, queue_snapshot = health._worker_health_snapshot(settings)

    assert ok is False
    assert queue_snapshot["missing"] == ["ai"]


def test_worker_health_accepts_merged_worker_when_ai_enabled(monkeypatch):
    _install_inspector(
        monkeypatch,
        {
            "worker@test": [
                "default",
                "ingest",
                "processing",
                "notifications",
                "maintenance",
                "ai",
            ],
        },
    )
    settings = SimpleNamespace(health_worker_ping_timeout_seconds=1.0, ai_enabled=True)

    ok, workers, queue_snapshot = health._worker_health_snapshot(settings)

    assert ok is True
    assert workers == {"worker@test": "pong"}
    assert queue_snapshot["missing"] == []


def test_worker_health_logs_dependency_type_without_standard_traceback(monkeypatch):
    warnings: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(
        health.celery_app.control,
        "inspect",
        lambda timeout: (_ for _ in ()).throw(RuntimeError("sensitive broker detail")),
    )
    monkeypatch.setattr(
        health.logger,
        "warning",
        lambda message, *args, **kwargs: warnings.append((message, args, kwargs)),
    )
    settings = SimpleNamespace(health_worker_ping_timeout_seconds=1.0, ai_enabled=False, log_detail="standard")

    ok, workers, queue_snapshot = health._worker_health_snapshot(settings)

    assert ok is False
    assert workers == {}
    assert queue_snapshot["missing"] == ["ingest", "processing", "notifications", "maintenance"]
    assert warnings == [
        (
            "worker_health_check_failed error_type=%s",
            ("RuntimeError",),
            {"exc_info": False},
        )
    ]
