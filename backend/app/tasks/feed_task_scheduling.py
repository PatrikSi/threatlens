from __future__ import annotations

from datetime import datetime, timedelta, timezone

from croniter import croniter

from app.models.feed import Feed


def is_feed_due(feed: Feed, now: datetime) -> bool:
    next_fetch_at = next_feed_fetch_at(feed, now)
    return next_fetch_at is not None and next_fetch_at <= now


def next_feed_fetch_at(feed: Feed, now: datetime) -> datetime | None:
    if not getattr(feed, "enabled", True):
        return None

    backoff_until = getattr(feed, "dispatch_backoff_until", None)
    claimed_at = getattr(feed, "dispatch_claimed_at", None)
    if backoff_until is not None and claimed_at is None:
        if backoff_until.tzinfo is None:
            backoff_until = backoff_until.replace(tzinfo=timezone.utc)
        if backoff_until > now:
            return backoff_until

    if feed.fetch_mode == "schedule":
        return next_scheduled_feed_fetch_at(feed, now)

    if feed.last_fetch_at is None:
        return now

    last_fetch_at = feed.last_fetch_at
    if last_fetch_at.tzinfo is None:
        last_fetch_at = last_fetch_at.replace(tzinfo=timezone.utc)

    raw_interval = getattr(feed, "fetch_interval_seconds", 1800)
    try:
        interval_seconds = int(raw_interval)
    except (TypeError, ValueError):
        interval_seconds = 1800
    interval_seconds = max(60, interval_seconds)

    next_fetch_at = last_fetch_at + timedelta(seconds=interval_seconds)
    return now if next_fetch_at <= now else next_fetch_at


def is_scheduled_feed_due(feed: Feed, now: datetime) -> bool:
    next_run = next_scheduled_feed_fetch_at(feed, now)
    return next_run is not None and next_run <= now


def next_scheduled_feed_fetch_at(feed: Feed, now: datetime) -> datetime | None:
    if not feed.schedule_cron:
        return None

    base = feed.last_fetch_at or now.replace(hour=0, minute=0, second=0, microsecond=0)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)

    if not croniter.is_valid(feed.schedule_cron):
        return None

    next_run = croniter(feed.schedule_cron, base).get_next(datetime)
    if next_run.tzinfo is None:
        next_run = next_run.replace(tzinfo=timezone.utc)
    return now if next_run <= now else next_run


def refresh_feed_next_fetch_at(feed: Feed, now: datetime) -> None:
    feed.next_fetch_at = next_feed_fetch_at(feed, now)
