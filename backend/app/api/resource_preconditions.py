from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from app.services.resource_versions import (
    as_utc,
    next_resource_version,
    resource_version_value,
)


class InvalidResourceVersion(ValueError):
    pass


class ResourceVersionMismatch(RuntimeError):
    pass


def require_matching_resource_version(
    *,
    current_updated_at: datetime,
    if_match: str | Sequence[str] | None,
) -> None:
    """Apply an optional If-Match precondition using an updated-at timestamp."""

    if if_match is None:
        return
    raw_values = [if_match] if isinstance(if_match, str) else list(if_match)
    if len(raw_values) == 1 and raw_values[0].strip() == "*":
        return
    candidates = [
        _parse_version_tag(value)
        for raw_value in raw_values
        for value in _split_version_tags(raw_value)
        if value.strip()
    ]
    if not candidates:
        raise InvalidResourceVersion("If-Match cannot be empty.")

    current_version = resource_version_tag(current_updated_at)
    for candidate in candidates:
        if candidate is None:
            continue
        if candidate == current_version:
            return
        if _canonical_timestamp_tag(candidate) == current_version:
            return
    raise ResourceVersionMismatch


def resource_version_tag(updated_at: datetime) -> str:
    return f'"{resource_version_value(updated_at)}"'


def _parse_version_tag(raw_value: str) -> str | None:
    value = raw_value.strip()
    if value.startswith("W/"):
        _validate_quoted_tag(value[2:])
        return None
    _validate_quoted_tag(value)
    return value


def _split_version_tags(raw_value: str) -> list[str]:
    tags: list[str] = []
    start = 0
    inside_quotes = False
    for index, character in enumerate(raw_value):
        if character == '"':
            inside_quotes = not inside_quotes
        elif character == "," and not inside_quotes:
            tags.append(raw_value[start:index])
            start = index + 1
    tags.append(raw_value[start:])
    return tags


def _validate_quoted_tag(value: str) -> None:
    if len(value) < 2 or value[0] != '"' or value[-1] != '"':
        raise InvalidResourceVersion(
            "If-Match resource versions must be quoted strong ETags."
        )
    opaque_value = value[1:-1]
    if any(not _is_etag_character(character) for character in opaque_value):
        raise InvalidResourceVersion("If-Match contains an invalid resource version.")


def _is_etag_character(character: str) -> bool:
    codepoint = ord(character)
    return codepoint == 0x21 or 0x23 <= codepoint <= 0x7E or 0x80 <= codepoint <= 0xFF


def _canonical_timestamp_tag(value: str) -> str | None:
    opaque_value = value[1:-1]
    if "T" not in opaque_value:
        return None
    try:
        parsed = datetime.fromisoformat(opaque_value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return resource_version_tag(as_utc(parsed))


__all__ = [
    "InvalidResourceVersion",
    "ResourceVersionMismatch",
    "next_resource_version",
    "require_matching_resource_version",
    "resource_version_tag",
]
