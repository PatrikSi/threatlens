from __future__ import annotations

from datetime import datetime, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def _load_migration_module():
    migration_path = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0022_settings_singletons_and_feed_url_normalization.py"
    spec = spec_from_file_location("migration_0022_settings_feed_urls", migration_path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_normalizes_feed_urls_without_dropping_query_strings():
    migration = _load_migration_module()

    assert migration._normalize_feed_url("HTTPS://Example.com:443/path/?token=abc123&source=partner#frag") == (
        "https://example.com/path?token=abc123&source=partner"
    )


def test_migration_normalizes_feed_urls_without_dropping_userinfo():
    migration = _load_migration_module()

    assert migration._normalize_feed_url("https://alice:secret@example.com:443/path/feed.xml?token=abc123#frag") == (
        "https://alice:secret@example.com/path/feed.xml?token=abc123"
    )


def test_migration_prefers_the_more_specific_feed_row_when_merging_duplicates():
    migration = _load_migration_module()

    canonical = {
        "id": "a",
        "name": "https://example.com/feed.xml",
        "url": "https://example.com/feed.xml",
        "description": None,
        "site_url": None,
        "language": None,
        "enabled": True,
        "fetch_mode": "interval",
        "fetch_interval_seconds": 1800,
        "schedule_cron": None,
        "etag": None,
        "last_modified": None,
        "last_fetch_at": datetime(2026, 4, 1, 9, 0, tzinfo=timezone.utc),
        "last_success_at": None,
        "error_count": 0,
        "last_error": None,
        "created_at": datetime(2026, 4, 1, 9, 0, tzinfo=timezone.utc),
    }
    duplicate = {
        "id": "b",
        "name": "Vendor Feed",
        "url": "https://example.com/feed.xml?token=abc123",
        "description": "Curated description",
        "site_url": "https://example.com",
        "language": "en",
        "enabled": False,
        "fetch_mode": "schedule",
        "fetch_interval_seconds": 7200,
        "schedule_cron": "0 * * * *",
        "etag": "etag-dup",
        "last_modified": "Wed, 26 Feb 2026 00:00:00 GMT",
        "last_fetch_at": datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc),
        "last_success_at": datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc),
        "error_count": 3,
        "last_error": "timeout",
        "created_at": datetime(2026, 4, 1, 8, 0, tzinfo=timezone.utc),
    }

    merged = migration._merge_feed_values(canonical, duplicate)

    assert merged["name"] == "Vendor Feed"
    assert merged["description"] == "Curated description"
    assert merged["site_url"] == "https://example.com"
    assert merged["language"] == "en"
    assert merged["etag"] == "etag-dup"
    assert merged["last_modified"] == "Wed, 26 Feb 2026 00:00:00 GMT"
    assert merged["last_error"] == "timeout"
    assert merged["error_count"] == 3
    assert "enabled" not in merged
    assert "fetch_mode" not in merged
    assert "fetch_interval_seconds" not in merged
    assert "schedule_cron" not in merged


def test_migration_prefers_the_more_specific_feed_row_as_canonical():
    migration = _load_migration_module()

    default_row = {
        "id": "a",
        "name": "https://example.com/feed.xml",
        "url": "https://example.com/feed.xml",
        "fetch_mode": "interval",
        "fetch_interval_seconds": 1800,
        "schedule_cron": None,
        "last_fetch_at": datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc),
        "last_success_at": None,
        "created_at": datetime(2026, 4, 1, 8, 0, tzinfo=timezone.utc),
    }
    specific_row = {
        "id": "b",
        "name": "Vendor Feed",
        "url": "https://example.com/feed.xml?token=abc123",
        "fetch_mode": "schedule",
        "fetch_interval_seconds": 7200,
        "schedule_cron": "0 * * * *",
        "last_fetch_at": datetime(2026, 4, 1, 9, 0, tzinfo=timezone.utc),
        "last_success_at": None,
        "created_at": datetime(2026, 4, 1, 7, 0, tzinfo=timezone.utc),
    }

    canonical = migration._select_canonical_feed_row([default_row, specific_row])

    assert canonical["id"] == "b"
