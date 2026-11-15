"""Recovery reads consumer progress before reserving durable wake-ups."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.core.worker_queues import QUEUE_NOTIFICATIONS
from app.services.queue_execution_canaries import QueueExecutionCanary
from app.tasks import integration_tasks


@pytest.mark.parametrize("kind", ["events", "deliveries"])
@pytest.mark.parametrize(
    "reason", ["fresh", "missing", "redis_unavailable", "invalid", "stale", "future"]
)
def test_dispatch_passes_notification_progress_before_opening_database(
    monkeypatch, kind, reason
):
    observed = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
    heartbeat = observed if reason == "fresh" else None
    retained_heartbeat = {
        "fresh": observed,
        "stale": observed - timedelta(minutes=10),
        "future": observed + timedelta(minutes=10),
    }.get(reason)
    order = []
    database = SimpleNamespace(commit=lambda: None)

    def read_progress(*, settings, queues):
        assert settings is integration_tasks.settings
        assert queues == [QUEUE_NOTIFICATIONS]
        order.append("canary")
        return {
            QUEUE_NOTIFICATIONS: QueueExecutionCanary(
                QUEUE_NOTIFICATIONS, reason, heartbeat_at=retained_heartbeat
            )
        }

    @contextmanager
    def session():
        order.append("database")
        yield database

    def reserve(db, **kwargs):
        assert db is database
        assert order == ["canary", "database"]
        assert kwargs == {"canary_at": heartbeat}
        return SimpleNamespace(
            event_ids=(), delivery_ids=(), reserved_at=observed
        )

    # The permissive patch lets the regression demonstrate missing wiring on
    # the previous implementation, without requiring a newly added import.
    monkeypatch.setattr(
        integration_tasks, "read_queue_execution_canaries", read_progress, raising=False
    )
    monkeypatch.setattr(integration_tasks, "db_session", session)
    monkeypatch.setattr(
        integration_tasks, f"reserve_recoverable_integration_{kind}", reserve
    )

    result = getattr(integration_tasks, f"dispatch_pending_integration_{kind}").run()

    assert result["scanned"] == result["queued"] == 0
    assert order == ["canary", "database"]
