from datetime import datetime, timezone
from types import SimpleNamespace

from app.tasks.feed_tasks import _is_feed_due


def test_interval_feed_due_when_elapsed():
    feed = SimpleNamespace(fetch_mode="interval", last_fetch_at=datetime(2026, 2, 26, 1, 0, tzinfo=timezone.utc), fetch_interval_seconds=60)
    now = datetime(2026, 2, 26, 1, 2, tzinfo=timezone.utc)
    assert _is_feed_due(feed, now)


def test_interval_feed_not_due_before_elapsed():
    feed = SimpleNamespace(fetch_mode="interval", last_fetch_at=datetime(2026, 2, 26, 1, 0, tzinfo=timezone.utc), fetch_interval_seconds=300)
    now = datetime(2026, 2, 26, 1, 2, tzinfo=timezone.utc)
    assert not _is_feed_due(feed, now)


def test_scheduled_feed_due_with_cron():
    feed = SimpleNamespace(
        fetch_mode="schedule",
        schedule_cron="0 * * * *",
        last_fetch_at=datetime(2026, 2, 26, 1, 0, tzinfo=timezone.utc),
        fetch_interval_seconds=1800,
    )
    now = datetime(2026, 2, 26, 2, 1, tzinfo=timezone.utc)
    assert _is_feed_due(feed, now)


def test_scheduled_feed_not_due_before_next_window():
    feed = SimpleNamespace(
        fetch_mode="schedule",
        schedule_cron="0 * * * *",
        last_fetch_at=datetime(2026, 2, 26, 1, 0, tzinfo=timezone.utc),
        fetch_interval_seconds=1800,
    )
    now = datetime(2026, 2, 26, 1, 30, tzinfo=timezone.utc)
    assert not _is_feed_due(feed, now)
