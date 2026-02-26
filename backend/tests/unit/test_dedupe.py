from datetime import datetime, timezone

from app.services.dedupe import content_hash, dedupe_key


def test_dedupe_key_prefers_guid():
    key = dedupe_key("feed-1", "guid-123", "https://example.com/a", "title", None)
    assert key == "guid:feed-1:guid-123"


def test_dedupe_key_falls_back_to_normalized_url():
    key = dedupe_key("feed-1", None, "https://example.com/a/?utm_source=x", "title", None)
    assert key == "url:https://example.com/a"


def test_content_hash_is_stable_for_case_changes():
    first = content_hash("Title", "Body", "https://example.com")
    second = content_hash("title", "body", "https://example.com")
    assert first == second


def test_hash_fallback_uses_title_date_domain():
    key = dedupe_key("feed-1", None, None, "My Post", datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert key.startswith("hash:")
