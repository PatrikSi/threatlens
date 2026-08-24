from __future__ import annotations

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

    candidates = [_parse_version_tag(value) for value in _split_version_tags(raw_value)]
    accepted_versions = {
        resource_version_tag(current_updated_at),
        f'"{as_utc(current_updated_at).isoformat()}"',
    }
    if current_updated_at.tzinfo is not None:
        accepted_versions.add(f'"{current_updated_at.isoformat()}"')
    if accepted_versions.isdisjoint(candidates):
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
    if not opaque_value or any(
        character == '"' or ord(character) < 0x21 or ord(character) > 0x7E
        for character in opaque_value
    ):
        raise InvalidResourceVersion(
            "If-Match contains an invalid resource version."
        )


__all__ = [
    "InvalidResourceVersion",
    "ResourceVersionMismatch",
    "next_resource_version",
    "require_matching_resource_version",
    "resource_version_tag",
]
