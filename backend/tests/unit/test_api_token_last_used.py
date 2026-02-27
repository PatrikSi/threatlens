from datetime import datetime, timedelta, timezone

from app.api.deps import _should_update_last_used


def test_should_update_last_used_when_missing():
    now = datetime.now(timezone.utc)
    assert _should_update_last_used(None, now)


def test_should_not_update_last_used_too_frequently():
    now = datetime.now(timezone.utc)
    recent = now - timedelta(seconds=30)
    assert not _should_update_last_used(recent, now)


def test_should_update_last_used_after_interval():
    now = datetime.now(timezone.utc)
    stale = now - timedelta(minutes=10)
    assert _should_update_last_used(stale, now)
