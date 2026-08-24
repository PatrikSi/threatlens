from __future__ import annotations

from datetime import datetime, timedelta, timezone


class InvalidResourceVersion(ValueError):
    pass


class ResourceVersionMismatch(RuntimeError):
    pass


def require_matching_resource_version(
    *,
    current_updated_at: datetime,
    if_match: str | None,
) -> None:
    """Apply an optional If-Match precondition using an updated-at timestamp."""

    if if_match is None:
        return
    raw_value = if_match.strip()
    if raw_value == "*":
        return
    if not raw_value:
        raise InvalidResourceVersion("If-Match cannot be empty.")

    candidates = [_parse_version_tag(value) for value in raw_value.split(",")]
    current = _as_utc(current_updated_at)
    if current not in candidates:
        raise ResourceVersionMismatch


def resource_version_tag(updated_at: datetime) -> str:
    return f'"{_as_utc(updated_at).isoformat()}"'


def next_resource_version(updated_at: datetime) -> datetime:
    current = _as_utc(updated_at)
    observed_at = datetime.now(timezone.utc)
    return max(observed_at, current + timedelta(microseconds=1))


def _parse_version_tag(raw_value: str) -> datetime:
    value = raw_value.strip()
    if value.startswith("W/"):
        raise InvalidResourceVersion(
            "If-Match requires a strong resource version, not a weak ETag."
        )
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1]
    if not value:
        raise InvalidResourceVersion(
            "If-Match must contain an ISO-8601 resource version."
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidResourceVersion(
            "If-Match must contain an ISO-8601 resource version."
        ) from exc
    if parsed.tzinfo is None:
        raise InvalidResourceVersion(
            "If-Match resource versions must include a UTC offset."
        )
    return _as_utc(parsed)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


__all__ = [
    "InvalidResourceVersion",
    "ResourceVersionMismatch",
    "next_resource_version",
    "require_matching_resource_version",
    "resource_version_tag",
]
