from __future__ import annotations

from datetime import datetime, timedelta, timezone


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def next_resource_version(
    current: datetime,
    *,
    observed_at: datetime | None = None,
) -> datetime:
    current_utc = as_utc(current)
    observed_utc = as_utc(observed_at or datetime.now(timezone.utc))
    return max(observed_utc, current_utc + timedelta(microseconds=1))


def resource_version_value(updated_at: datetime) -> str:
    return as_utc(updated_at).isoformat().replace("+00:00", "Z")


__all__ = ["as_utc", "next_resource_version", "resource_version_value"]
