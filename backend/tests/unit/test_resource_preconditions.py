from datetime import datetime, timezone

import pytest

from app.api.resource_preconditions import (
    InvalidResourceVersion,
    ResourceVersionMismatch,
    next_resource_version,
    require_matching_resource_version,
    resource_version_tag,
)


def test_resource_version_accepts_equivalent_utc_timestamps():
    updated_at = datetime(2026, 8, 24, 9, 30, 12, 345678, tzinfo=timezone.utc)

    require_matching_resource_version(
        current_updated_at=updated_at,
        if_match='"2026-08-24T11:30:12.345678+02:00"',
    )

    assert resource_version_tag(updated_at) == '"2026-08-24T09:30:12.345678+00:00"'


def test_resource_version_rejects_stale_and_malformed_preconditions():
    updated_at = datetime(2026, 8, 24, 9, 30, tzinfo=timezone.utc)

    with pytest.raises(ResourceVersionMismatch):
        require_matching_resource_version(
            current_updated_at=updated_at,
            if_match='"2026-08-24T09:29:00Z"',
        )
    with pytest.raises(InvalidResourceVersion, match="UTC offset"):
        require_matching_resource_version(
            current_updated_at=updated_at,
            if_match='"2026-08-24T09:30:00"',
        )
    with pytest.raises(InvalidResourceVersion, match="strong resource version"):
        require_matching_resource_version(
            current_updated_at=updated_at,
            if_match='W/"2026-08-24T09:30:00Z"',
        )


def test_resource_version_remains_optional_for_older_clients():
    updated_at = datetime(2026, 8, 24, 9, 30, tzinfo=timezone.utc)

    require_matching_resource_version(current_updated_at=updated_at, if_match=None)
    require_matching_resource_version(current_updated_at=updated_at, if_match="*")


def test_next_resource_version_is_strictly_monotonic():
    future_version = datetime(2099, 8, 24, 9, 30, tzinfo=timezone.utc)

    assert next_resource_version(future_version) > future_version
