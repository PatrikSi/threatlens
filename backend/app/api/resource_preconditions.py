from __future__ import annotations

from datetime import datetime

from app.services.resource_versions import (
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

    current = resource_version_tag(current_updated_at)
    candidates = [_parse_version_tag(value) for value in raw_value.split(",")]
    if current not in candidates:
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
